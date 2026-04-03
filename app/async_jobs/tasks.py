"""Celery tasks for workflow execution."""

import asyncio
from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.async_jobs.celery_app import app
from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from app.database.connection import SessionLocal
from app.database.models import Session as SessionModel
from app.database.models import SessionStatus
from app.services.embedding.exceptions import ChromaConnectionError
from app.services.embedding.factory import get_embedding_service
from app.services.embedding.service import EmbeddingService
from app.workflows.execution_context import (
    GenerationState,
    WorkflowRegistry,
    resolve_target_to_workflow_class,
)
from app.workflows.services.execution_service import WorkflowExecutionService

logger = structlog.get_logger()


def _is_transient_error(exception: Exception) -> bool:
    """
    Determine if an error is transient and worth retrying.

    Whitelist approach: only retry on known transient errors that are likely
    to succeed on a subsequent attempt. Programming errors, data validation
    errors, and API errors (non-rate-limit) should NOT be retried.

    Args:
        exception: The exception that occurred

    Returns:
        True if error is transient and should be retried, False otherwise
    """
    error_str = str(exception)
    error_type = type(exception).__name__

    # RETRYABLE: External service temporary failures
    retryable_errors = [
        # Network issues
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "Timeout",
        "TimeoutError",
        "socket.timeout",
        "BrokenPipeError",
        "RemoteDisconnected",
        # Rate limiting (HTTP 429, 503)
        "429",  # Too Many Requests
        "503",  # Service Unavailable
        "rate_limit",
        "RateLimitError",
        "TooManyRequestsError",
        # Database connection issues (transient)
        "OperationalError",
        "DatabaseError",
        "connection refused",
        "connection reset",
        "too many connections",
        # Task queue connection issues
        "AMQPConnectionError",
        "AMQPChannelError",
    ]

    # NON-RETRYABLE: Programming/data model errors
    non_retryable_errors = [
        # Data integrity
        "IntegrityError",
        "UniqueViolation",
        "ForeignKeyViolation",
        "duplicate key value",
        "not-null violation",
        # Validation/input errors
        "ValueError",
        "TypeError",
        "AttributeError",
        "KeyError",
        "IndexError",
        # File/resource errors
        "FileNotFoundError",
        "PermissionError",
        "IsADirectoryError",
        # Business logic errors
        "AssertionError",
        "NotImplementedError",
        # These indicate the task itself has a bug
        "NameError",
        "SyntaxError",
        "IndentationError",
    ]

    # Check if it's a known non-retryable error
    for non_retryable in non_retryable_errors:
        if non_retryable in error_type or non_retryable in error_str:
            logger.debug(
                "non_retryable_error_detected",
                error_type=error_type,
                matched_pattern=non_retryable,
            )
            return False

    # Check if it's a known retryable error
    for retryable in retryable_errors:
        if retryable in error_type or retryable in error_str:
            logger.debug(
                "transient_error_detected",
                error_type=error_type,
                matched_pattern=retryable,
            )
            return True

    # Unknown error - don't retry (safer to fail fast than risk infinite loops)
    logger.debug(
        "unknown_error_type_not_retried",
        error_type=error_type,
        error_str=error_str[:100],
        reason="Unknown error types are not retried to avoid masking programming errors",
    )
    return False


async def _generate_session_embedding_base(
    session_id: int,
    embedding_text: str | None = None,
) -> None:
    """
    Generate embedding for a session.

    Performs the embedding pipeline: validate config → fetch service → fetch session →
    prepare/validate text → generate embedding → store in Chroma → update metadata.

    This is an async helper function, not a Celery task (no retry logic here).

    Args:
        session_id: Session ID to embed
        embedding_text: Pre-computed text to embed (if None, will fetch from session)

    Raises:
        ValueError: If session not found or text validation fails
        Exception: If embedding or storage fails
    """
    from app.config.settings import get_settings

    db: Session | None = None
    try:
        # Check if embeddings are enabled
        if not get_settings().enable_embeddings:
            logger.info(
                "embedding_generation_disabled",
                session_id=session_id,
            )
            return

        # Get embedding service from factory
        try:
            service = get_embedding_service()
        except ChromaConnectionError as e:
            logger.error(
                "embedding_service_initialization_failed",
                session_id=session_id,
                error=str(e),
            )
            raise

        db = SessionLocal()

        # Fetch session
        session = session_crud.read(db, session_id)
        if not session:
            logger.warning(
                "embedding_session_not_found",
                session_id=session_id,
            )
            return

        logger.info(
            "embedding_generation_started",
            session_id=session_id,
        )

        # Prepare text if not provided
        if embedding_text is None:
            embedding_text = service.prepare_session_text_with_summary(session)

        # Validate text
        if not EmbeddingService.validate_embedding_text(embedding_text):
            logger.warning(
                "embedding_text_validation_failed",
                session_id=session_id,
                text_length=len(embedding_text) if embedding_text else 0,
            )
            return

        # Generate embedding
        embedding = await service.embed_query(embedding_text)

        # Store embedding in Chroma with metadata built from session
        await service.store_session_embedding(
            session_id, embedding, embedding_text, session=session
        )

        # Update embedding metadata in database
        session.embedding_model = get_settings().embedding_model_name
        session.embedding_generated_at = datetime.utcnow()
        db.add(session)
        db.commit()

        logger.info(
            "embedding_generation_completed",
            session_id=session_id,
            embedding_model=get_settings().embedding_model_name,
            embedding_dimension=len(embedding),
        )

    except Exception as e:
        logger.error(
            "embedding_generation_failed",
            session_id=session_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise

    finally:
        if db:
            db.close()


@app.task(
    name="app.async_jobs.tasks.generate_session_embedding",
    bind=True,
    max_retries=2,
    queue="embeds",
)
def generate_session_embedding(
    self,
    session_id: int,
    embedding_text: str | None = None,
):
    """
    Generate and store embedding for a session asynchronously.

    Args:
        session_id: Session ID to embed
        embedding_text: Pre-computed text to embed (if None, will fetch from session)
    """
    db: Session | None = None
    try:
        # Run the async helper
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _generate_session_embedding_base(
                    session_id=session_id,
                    embedding_text=embedding_text,
                )
            )
        finally:
            loop.close()

    except Exception as e:
        logger.error(
            "embedding_generation_failed_at_task_level",
            session_id=session_id,
            error=str(e),
            error_type=type(e).__name__,
        )

        # Retry on transient errors only
        if _is_transient_error(e):
            logger.info(
                "embedding_generation_retrying",
                task_id=self.request.id,
                session_id=session_id,
                retry_count=self.request.retries,
            )
            raise self.retry(exc=e, countdown=60 * (self.request.retries + 1)) from e

    finally:
        if db:
            db.close()


def _resolve_and_build_workflow(target: str, execution_id: int):
    """
    Resolve workflow target and build execution graph.

    Separated to reduce main task complexity.

    Args:
        target: Workflow name or step identifier
        execution_id: For logging context

    Returns:
        Compiled LangGraph workflow graph

    Raises:
        Exception: If target resolution or graph building fails
    """
    try:
        logger.info(
            "resolving_workflow_target",
            target=target,
            execution_id=execution_id,
        )
        workflow_class = resolve_target_to_workflow_class(target)
        logger.info(
            "workflow_target_resolved",
            target=target,
            workflow_class=workflow_class.__name__,
            execution_id=execution_id,
        )
    except Exception as resolve_error:
        logger.error(
            "workflow_target_resolution_failed",
            target=target,
            execution_id=execution_id,
            error=str(resolve_error),
            error_type=type(resolve_error).__name__,
            exc_info=True,
        )
        raise

    try:
        graph = WorkflowRegistry.get_or_build_graph(workflow_class)
        logger.info(
            "content_generation_workflow_graph_loaded",
            execution_id=execution_id,
            target=target,
            graph_nodes=list(graph.nodes) if hasattr(graph, "nodes") else "N/A",
        )
        return graph
    except Exception as graph_error:
        logger.error(
            "workflow_graph_build_failed",
            target=target,
            execution_id=execution_id,
            workflow_class=workflow_class.__name__ if "workflow_class" in locals() else "unknown",
            error=str(graph_error),
            error_type=type(graph_error).__name__,
            exc_info=True,
        )
        raise


def _execute_workflow_graph(
    graph, initial_state: GenerationState, execution_id: int, session_id: int, task_id: str
):
    """
    Execute LangGraph workflow asynchronously.

    Separated to reduce main task complexity.

    Args:
        graph: Compiled LangGraph workflow
        initial_state: Initial GenerationState
        execution_id: For logging
        session_id: For logging
        task_id: Celery task ID for logging

    Returns:
        Final state dict after graph execution

    Raises:
        Exception: If graph execution fails
    """
    logger.info(
        "content_generation_starting_execution",
        task_id=task_id,
        execution_id=execution_id,
        session_id=session_id,
        initial_state_keys=list(initial_state.keys()),
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        logger.info(
            "about_to_invoke_graph",
            execution_id=execution_id,
        )

        final_state = loop.run_until_complete(graph.ainvoke(initial_state))

        logger.info(
            "content_generation_execution_completed",
            task_id=task_id,
            execution_id=execution_id,
            session_id=session_id,
            generated_content_keys=list(final_state.keys()),
        )
        return final_state
    except Exception as exec_error:
        logger.error(
            "graph_execution_failed",
            execution_id=execution_id,
            error=str(exec_error),
            error_type=type(exec_error).__name__,
            exc_info=True,
        )
        raise
    finally:
        loop.close()


def _track_generated_content(
    final_state: dict, execution_id: int, session_id: int, db: Session
) -> list[int]:
    """
    Update session with generated content identifiers and retrieve created content IDs.

    Separated to reduce main task complexity.

    Args:
        final_state: Final state dict from graph execution
        execution_id: WorkflowExecution ID
        session_id: Session ID
        db: Database session

    Returns:
        List of created content IDs
    """
    # Extract step identifiers from final state (other keys are execution context)
    # Fixed SIM118: use 'in dict' instead of 'in dict.keys()'
    step_identifiers = [
        k for k in final_state if k not in ["session_id", "execution_id", "transcription"]
    ]

    for step_id in step_identifiers:
        if final_state.get(step_id):
            session_crud.add_available_content_identifier(db, session_id, step_id)
            logger.debug(
                "content_identifier_ensured_in_session",
                session_id=session_id,
                step_id=step_id,
            )

    # Get created content IDs for logging
    created_content = content_crud.get_content_list(db, session_id)
    created_ids = [c.id for c in created_content if c.workflow_execution_id == execution_id]

    logger.info(
        "created_content_retrieved_for_logging",
        execution_id=execution_id,
        session_id=session_id,
        created_ids=created_ids,
    )

    return created_ids


def _handle_workflow_error(
    e: Exception,
    execution_id: int,
    session_id: int,
    target: str,
    task_id: str,
    task_self,
    db: Session | None = None,
):
    """
    Handle workflow execution errors with proper logging and retry logic.

    Separated to reduce main task complexity.

    Args:
        e: Exception that occurred
        execution_id: WorkflowExecution ID
        session_id: Session ID
        target: Workflow target
        task_id: Celery task ID
        task_self: Celery task self object (for retry)
        db: Optional database session
    """
    import traceback

    tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
    tb_str = "".join(tb_lines)

    should_retry = _is_transient_error(e)

    logger.error(
        "content_generation_task_failed",
        task_id=task_id,
        execution_id=execution_id,
        session_id=session_id,
        target=target,
        error=str(e),
        error_type=type(e).__name__,
        should_retry=should_retry,
        exc_info=True,
        full_traceback=tb_str,
    )

    # Mark as failed
    if db:
        try:
            WorkflowExecutionService.mark_failed(execution_id, db, str(e))
            logger.info(
                "content_generation_execution_marked_failed",
                execution_id=execution_id,
                error=str(e),
            )
        except Exception as db_error:
            logger.error(
                "failed_to_mark_workflow_failed",
                error=str(db_error),
                original_error=str(e),
            )

    # Only retry on transient errors
    if should_retry:
        try:
            logger.info(
                "content_generation_task_retrying",
                task_id=task_id,
                retry_count=task_self.request.retries,
                max_retries=task_self.max_retries,
                countdown_seconds=60 * (task_self.request.retries + 1),
                error_type=type(e).__name__,
            )
            raise task_self.retry(exc=e, countdown=60 * (task_self.request.retries + 1)) from e
        except Exception as retry_exception:
            logger.error(
                "content_generation_task_max_retries_exceeded",
                task_id=task_id,
                execution_id=execution_id,
                final_error=str(retry_exception),
            )
    else:
        logger.error(
            "content_generation_task_not_retried_programming_error",
            task_id=task_id,
            execution_id=execution_id,
            error_type=type(e).__name__,
            reason="Error is not transient (programming error). Permanent failure recorded.",
        )


@app.task(
    name="app.async_jobs.tasks.execute_generated_content",
    bind=True,
    max_retries=2,
    queue="workflows",
)
def execute_generated_content(
    self,
    session_id: int,
    execution_id: int,
    target: str,
    triggered_by: str = "user_triggered",
    created_by_user_id: int | None = None,
):
    """
    Execute a content generation workflow using LangGraph.

    This task handles the actual execution of workflow steps through the LangGraph graph.
    Steps handle their own content persistence, so this task orchestrates the execution
    and manages the overall execution status.

    Args:
        session_id: Session ID to process
        execution_id: WorkflowExecution record ID for tracking
        target: Workflow name or step identifier (e.g., "talk_workflow", "summary")
        triggered_by: "user_triggered" or "auto_scheduled"
        created_by_user_id: User who triggered the workflow (stored in WorkflowExecution)
    """
    db: Session | None = None
    try:
        db = SessionLocal()

        logger.info(
            "content_generation_task_starting",
            task_id=self.request.id,
            session_id=session_id,
            execution_id=execution_id,
            target=target,
            triggered_by=triggered_by,
            created_by_user_id=created_by_user_id,
        )

        # Mark as running and store user context
        WorkflowExecutionService.mark_running(execution_id, db, self.request.id)
        if created_by_user_id is not None:
            workflow_exec = content_crud.get_workflow_execution(db, execution_id)
            if workflow_exec:
                workflow_exec.created_by_user_id = created_by_user_id
                db.commit()
                logger.info(
                    "workflow_execution_user_context_stored",
                    execution_id=execution_id,
                    created_by_user_id=created_by_user_id,
                )

        # Fetch transcription if available (optional - steps define their own dependencies)
        tx_content = content_crud.get_content_by_identifier(db, session_id, "transcription")
        if not tx_content:
            logger.warning(
                "content_generation_transcription_not_found",
                task_id=self.request.id,
                execution_id=execution_id,
                session_id=session_id,
            )

        logger.info(
            "content_generation_transcription_loaded",
            task_id=self.request.id,
            execution_id=execution_id,
            session_id=session_id,
            transcription_available=tx_content is not None,
        )

        # Build initial state for LangGraph
        initial_state: GenerationState = GenerationState(
            session_id=session_id,
            execution_id=execution_id,
            transcription=tx_content.content if tx_content else None,
        )

        # Validate that db is NOT in state (common cause of serialization errors)
        if "db" in initial_state:
            logger.error(
                "invalid_state_contains_db",
                execution_id=execution_id,
                db_value=repr(initial_state.get("db"))[:100],
            )
            raise ValueError(
                "BUG: Database session was included in GenerationState. "
                "This will cause serialization failures. "
                "Each step should create its own SessionLocal() instance."
            )

        logger.info(
            "content_generation_initial_state_built",
            task_id=self.request.id,
            execution_id=execution_id,
            session_id=session_id,
            state_keys=list(initial_state.keys()),
        )

        # Resolve workflow and build graph
        graph = _resolve_and_build_workflow(target, execution_id)

        # Execute graph
        final_state = _execute_workflow_graph(
            graph,
            initial_state,
            execution_id,
            session_id,
            self.request.id,
        )

        # Track generated content and update session
        created_ids = _track_generated_content(final_state, execution_id, session_id, db)

        # Mark as completed
        WorkflowExecutionService.mark_completed(execution_id, db, created_ids)

        # Extract step identifiers for summary logging
        step_identifiers = [
            k for k in final_state if k not in ["session_id", "execution_id", "transcription"]
        ]

        logger.info(
            "content_generation_task_completed",
            task_id=self.request.id,
            execution_id=execution_id,
            session_id=session_id,
            target=target,
            step_identifiers=step_identifiers,
            created_ids=created_ids,
            total_duration_seconds=(
                (
                    datetime.utcnow()
                    - content_crud.get_workflow_execution(db, execution_id).created_at
                ).total_seconds()
                if content_crud.get_workflow_execution(db, execution_id)
                else 0
            ),
        )

        return {
            "status": "completed",
            "execution_id": execution_id,
            "created_ids": created_ids,
        }

    except Exception as e:
        _handle_workflow_error(e, execution_id, session_id, target, self.request.id, self, db)

    finally:
        if db:
            db.close()
            logger.debug(
                "database_session_closed",
                execution_id=execution_id,
            )


@app.task(
    name="app.async_jobs.tasks.delete_session_embedding",
    bind=True,
    max_retries=2,
    queue="embeds",
)
def delete_session_embedding(self, session_id: int):
    """
    Delete embedding for a session asynchronously.

    Args:
        session_id: Session ID whose embedding should be deleted
    """
    try:
        embedding_service = get_embedding_service()

        asyncio.run(embedding_service.delete_session_embedding(session_id))

        logger.info(
            "session_embedding_deleted",
            session_id=session_id,
        )
    except Exception as exc:
        logger.error(
            "delete_session_embedding_failed",
            session_id=session_id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60) from exc


# Health check task
@app.task(name="app.async_jobs.tasks.health_check")
def health_check():
    """Health check task for Celery worker."""
    logger.info(
        "celery_health_check_executed",
        status="ok",
        worker_name=(
            health_check.request.hostname if hasattr(health_check, "request") else "unknown"
        ),
    )
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.task(
    name="app.async_jobs.tasks.reconcile_session_embeddings",
    bind=True,
    max_retries=1,
    queue="embeds",
)
def reconcile_session_embeddings(  # noqa: C901
    self,
    event_id: int | None = None,
    enqueue_refreshes: bool = True,
):
    """
    Reconcile published sessions against Chroma vectors and queue embedding refreshes.

    Detects:
    - missing embeddings: published session exists in DB but vector is absent in Chroma
    - stale embeddings: vector metadata source_updated_at older than DB updated_at
    """
    from app.config.settings import get_settings

    settings = get_settings()
    task_kwargs = getattr(getattr(self, "request", None), "kwargs", {}) or {}
    if event_id is None:
        event_id = task_kwargs.get("event_id")
    if not enqueue_refreshes:
        enqueue_refreshes = bool(task_kwargs.get("enqueue_refreshes", False))
    if not settings.enable_embeddings or not settings.embedding_sync_enabled:
        logger.info(
            "embedding_reconcile_skipped_disabled",
            embeddings_enabled=settings.enable_embeddings,
            sync_enabled=settings.embedding_sync_enabled,
        )
        return {
            "status": "skipped",
            "reason": "disabled",
        }

    db: Session | None = None
    try:
        embedding_service = get_embedding_service()
        if embedding_service is None:
            logger.info("embedding_reconcile_skipped_no_service")
            return {
                "status": "skipped",
                "reason": "service_unavailable",
            }

        batch_size = max(1, settings.embedding_sync_batch_size)
        max_enqueues = max(1, settings.embedding_sync_max_enqueues_per_run)
        stale_threshold = max(0, settings.embedding_sync_stale_threshold_seconds)

        db = SessionLocal()

        scanned = 0
        missing = 0
        stale = 0
        to_reembed = 0
        queued = 0
        offset = 0
        enqueue_cap_reached = False

        while True:
            query = db.query(SessionModel.id, SessionModel.updated_at).filter(
                SessionModel.status == SessionStatus.PUBLISHED
            )
            if event_id is not None:
                query = query.filter(SessionModel.event_id == event_id)

            rows = query.order_by(SessionModel.id).offset(offset).limit(batch_size).all()
            if not rows:
                break

            scanned += len(rows)
            session_ids = [row[0] for row in rows]
            updated_at_map = {row[0]: row[1] for row in rows}

            chroma_ids = [f"session_{sid}" for sid in session_ids]
            chroma_results = embedding_service.sessions_collection.get(
                ids=chroma_ids,
                include=["metadatas"],
            )

            result_ids = chroma_results.get("ids") or []
            result_metadatas = chroma_results.get("metadatas") or []
            metadata_by_session_id: dict[int, dict] = {}
            for chroma_id, metadata in zip(result_ids, result_metadatas, strict=False):
                if not chroma_id.startswith("session_"):
                    continue
                sid = int(chroma_id.split("_")[1])
                metadata_by_session_id[sid] = metadata or {}

            for session_id in session_ids:
                metadata = metadata_by_session_id.get(session_id)
                needs_refresh = False

                if metadata is None:
                    missing += 1
                    needs_refresh = True
                else:
                    db_updated_at = updated_at_map.get(session_id)
                    db_updated_ts = db_updated_at.timestamp() if db_updated_at else None
                    source_updated_at = metadata.get("source_updated_at")

                    if db_updated_ts is not None and (
                        source_updated_at is None
                        or float(source_updated_at) + stale_threshold < db_updated_ts
                    ):
                        stale += 1
                        needs_refresh = True

                if needs_refresh:
                    to_reembed += 1
                    if enqueue_refreshes and queued < max_enqueues:
                        generate_session_embedding.apply_async(args=[session_id], queue="embeds")
                        queued += 1
                    elif enqueue_refreshes:
                        enqueue_cap_reached = True

            offset += batch_size

        if enqueue_cap_reached:
            logger.warning(
                "embedding_reconcile_enqueue_cap_reached",
                max_enqueues=max_enqueues,
                total_needing_reembed=to_reembed,
            )

        synced = max(0, scanned - to_reembed)

        logger.info(
            "embedding_reconcile_completed",
            event_id=event_id,
            scanned_published_sessions=scanned,
            synced_embeddings=synced,
            missing_embeddings=missing,
            stale_embeddings=stale,
            total_needing_reembed=to_reembed,
            queued_refreshes=queued,
            enqueue_refreshes=enqueue_refreshes,
            batch_size=batch_size,
            max_enqueues=max_enqueues,
            stale_threshold_seconds=stale_threshold,
        )
        return {
            "status": "ok",
            "event_id": event_id,
            "scanned": scanned,
            "synced": synced,
            "missing": missing,
            "stale": stale,
            "to_reembed": to_reembed,
            "queued": queued,
            "enqueue_refreshes": enqueue_refreshes,
            "max_enqueues": max_enqueues,
        }
    except Exception as exc:
        logger.error(
            "embedding_reconcile_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        if _is_transient_error(exc):
            raise self.retry(exc=exc, countdown=300) from exc
        raise
    finally:
        if db:
            db.close()
