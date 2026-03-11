"""Celery tasks for workflow execution."""

from datetime import datetime
from typing import Optional, Callable, Dict, Any
import asyncio
import structlog
from sqlalchemy.orm import Session

from app.async_jobs.celery_app import app
from app.database.connection import SessionLocal
from app.workflows.execution_context import (
    GenerationState,
    resolve_target_to_workflow_class,
    WorkflowRegistry,
)
from app.workflows.services.execution_service import WorkflowExecutionService
from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from app.services.embedding_service import EmbeddingService
from app.services.embedding_factory import get_embedding_service
from app.services.embedding_exceptions import ChromaConnectionError
from app.constants.embedding import ENTITY_TYPE_SESSION, ENTITY_TYPE_EVENT

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


def _prepare_embedding_text_factory(entity_type: str) -> Callable[[Any, ...], str]:
    """
    Factory for text preparation functions based on entity type.

    Args:
        entity_type: ENTITY_TYPE_SESSION or ENTITY_TYPE_EVENT

    Returns:
        Function to prepare text for given entity type
    """
    from app.crud import event as event_crud_module

    if entity_type == ENTITY_TYPE_SESSION:

        def prepare_session(service, entity):
            # Try to fetch summary as fallback
            summary_content = None
            db = SessionLocal()
            try:
                summary_content = content_crud.get_content_by_identifier(
                    db, entity.id, "summary"
                )
            except Exception as e:
                logger.debug(
                    "embedding_summary_fetch_failed",
                    entity_id=entity.id,
                    error=str(e),
                )
            finally:
                db.close()

            summary_text = summary_content.content if summary_content else None
            return service._prepare_session_text(
                title=entity.title,
                short_description=entity.short_description,
                summary=summary_text,
            )

        return prepare_session

    elif entity_type == ENTITY_TYPE_EVENT:

        def prepare_event(service, entity):
            return service._prepare_event_text(
                title=entity.title,
                description=entity.description,
            )

        return prepare_event

    else:
        raise ValueError(f"Unknown entity type: {entity_type}")


def _crud_mapper(entity_type: str) -> Dict[str, Any]:
    """
    Map entity type to CRUD operations and collection methods.

    Args:
        entity_type: ENTITY_TYPE_SESSION or ENTITY_TYPE_EVENT

    Returns:
        Dict with: crud_read, store_fn, log_prefix, entity_name
    """
    from app.crud import event as event_crud_module

    if entity_type == ENTITY_TYPE_SESSION:
        return {
            "read": session_crud.read,
            "store": lambda svc, entity_id, emb, text: svc.store_session_embedding(
                entity_id, emb, text
            ),
            "log_prefix": "session",
            "entity_name": "session",
        }
    elif entity_type == ENTITY_TYPE_EVENT:
        return {
            "read": event_crud_module.event_crud.read,
            "store": lambda svc, entity_id, emb, text: svc.store_event_embedding(
                entity_id, emb, text
            ),
            "log_prefix": "event",
            "entity_name": "event",
        }
    else:
        raise ValueError(f"Unknown entity type: {entity_type}")


async def _generate_embedding_base(
    entity_type: str,
    entity_id: int,
    embedding_text: Optional[str] = None,
) -> None:
    """
    Generic embedding generation logic for both sessions and events.

    Performs the embedding pipeline: validate config → fetch service → fetch entity →
    prepare/validate text → generate embedding → store in Chroma → update metadata.

    This is an async helper function, not a Celery task (no retry logic here).

    Args:
        entity_type: ENTITY_TYPE_SESSION or ENTITY_TYPE_EVENT
        entity_id: ID of entity to embed
        embedding_text: Pre-computed text to embed (if None, will fetch from entity)

    Raises:
        ValueError: If entity not found or text validation fails
        Exception: If embedding or storage fails
    """
    from app.config.settings import get_settings

    db: Optional[Session] = None
    try:
        # Check if embeddings are enabled
        if not get_settings().enable_embeddings:
            logger.info(
                f"embedding_generation_disabled",
                **{f"{entity_type}_id": entity_id},
            )
            return

        # Get embedding service from factory
        try:
            service = get_embedding_service()
        except ChromaConnectionError as e:
            logger.error(
                "embedding_service_initialization_failed",
                **{f"{entity_type}_id": entity_id},
                error=str(e),
            )
            raise

        db = SessionLocal()

        # Get CRUD operations for entity type
        crud_ops = _crud_mapper(entity_type)

        # Fetch entity
        entity = crud_ops["read"](db, entity_id)
        if not entity:
            logger.warning(
                f"embedding_{entity_type}_not_found",
                **{f"{entity_type}_id": entity_id},
            )
            return

        logger.info(
            "embedding_generation_started",
            entity_type=entity_type,
            **{f"{entity_type}_id": entity_id},
        )

        # Prepare text if not provided
        if embedding_text is None:
            prepare_fn = _prepare_embedding_text_factory(entity_type)
            embedding_text = prepare_fn(service, entity)

        # Validate text
        if not EmbeddingService.validate_embedding_text(embedding_text):
            logger.warning(
                "embedding_text_validation_failed",
                entity_type=entity_type,
                **{f"{entity_type}_id": entity_id},
                text_length=len(embedding_text) if embedding_text else 0,
            )
            return

        # Generate embedding
        embedding = await service.embed_query(embedding_text)

        # Store embedding in Chroma (async)
        await crud_ops["store"](service, entity_id, embedding, embedding_text)

        # Update embedding metadata in database
        entity.embedding_model = get_settings().embedding_model_name
        entity.embedding_generated_at = datetime.utcnow()
        db.add(entity)
        db.commit()

        logger.info(
            "embedding_generation_completed",
            entity_type=entity_type,
            **{f"{entity_type}_id": entity_id},
            embedding_model=get_settings().embedding_model_name,
            embedding_dimension=len(embedding),
        )

    except Exception as e:
        logger.error(
            "embedding_generation_failed",
            entity_type=entity_type,
            **{f"{entity_type}_id": entity_id},
            error=str(e),
            error_type=type(e).__name__,
        )
        raise

    finally:
        if db:
            db.close()


@app.task(
    name="app.async_jobs.tasks.generate_embedding",
    bind=True,
    max_retries=2,
    queue="embeds",
)
def generate_embedding(
    self,
    entity_type: str,
    entity_id: int,
    embedding_text: Optional[str] = None,
):
    """
    Generic Celery task for embedding generation (session or event).

    Wraps the async helper with retry logic and error handling specific to Celery.
    Silently fails - embedding should never block entity operations.

    Args:
        entity_type: ENTITY_TYPE_SESSION or ENTITY_TYPE_EVENT
        entity_id: ID of entity to embed
        embedding_text: Pre-computed text to embed (if None, will fetch from entity)
    """
    db: Optional[Session] = None
    try:
        # Run the async helper
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _generate_embedding_base(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    embedding_text=embedding_text,
                )
            )
        finally:
            loop.close()

    except Exception as e:
        logger.error(
            "embedding_generation_failed_at_task_level",
            entity_type=entity_type,
            **{f"{entity_type}_id": entity_id},
            error=str(e),
            error_type=type(e).__name__,
        )

        # Retry on transient errors only
        if _is_transient_error(e):
            logger.info(
                "embedding_generation_retrying",
                task_id=self.request.id,
                entity_type=entity_type,
                **{f"{entity_type}_id": entity_id},
                retry_count=self.request.retries,
            )
            raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))

    finally:
        if db:
            db.close()


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
    created_by_user_id: Optional[int] = None,
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
        created_by_user_id: User who triggered the workflow
    """
    db: Optional[Session] = None
    try:
        db = SessionLocal()

        logger.info(
            "content_generation_task_starting",
            task_id=self.request.id,
            session_id=session_id,
            execution_id=execution_id,
            target=target,
            triggered_by=triggered_by,
        )

        # Mark as running
        WorkflowExecutionService.mark_running(execution_id, db, self.request.id)

        logger.info(
            "content_generation_marked_running",
            task_id=self.request.id,
            execution_id=execution_id,
            session_id=session_id,
        )

        # Fetch transcription and session data
        tx_content = content_crud.get_content_by_identifier(
            db, session_id, "transcription"
        )
        if not tx_content:
            raise ValueError(f"Transcription not found for session {session_id}")

        logger.info(
            "content_generation_transcription_loaded",
            task_id=self.request.id,
            execution_id=execution_id,
            session_id=session_id,
        )

        # Build initial state for LangGraph
        initial_state: GenerationState = GenerationState(
            session_id=session_id,
            execution_id=execution_id,
            transcription=tx_content.content,
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

        # Resolve target to workflow class and build graph
        logger.info(
            "resolving_workflow_target",
            target=target,
            session_id=session_id,
        )

        try:
            workflow_class = resolve_target_to_workflow_class(target)
            logger.info(
                "workflow_target_resolved",
                target=target,
                workflow_class=workflow_class.__name__,
                session_id=session_id,
            )
        except Exception as resolve_error:
            logger.error(
                "workflow_target_resolution_failed",
                target=target,
                error=str(resolve_error),
                error_type=type(resolve_error).__name__,
                exc_info=True,
            )
            raise

        # Get or build workflow graph
        try:
            graph = WorkflowRegistry.get_or_build_graph(workflow_class)
            logger.info(
                "content_generation_workflow_graph_loaded",
                task_id=self.request.id,
                execution_id=execution_id,
                session_id=session_id,
                target=target,
                graph_nodes=list(graph.nodes) if hasattr(graph, "nodes") else "N/A",
            )
        except Exception as graph_error:
            logger.error(
                "workflow_graph_build_failed",
                target=target,
                workflow_class=workflow_class.__name__,
                error=str(graph_error),
                error_type=type(graph_error).__name__,
                exc_info=True,
            )
            raise

        # Execute graph (async, so we need asyncio)
        logger.info(
            "content_generation_starting_execution",
            task_id=self.request.id,
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
                target=target,
            )

            final_state = loop.run_until_complete(graph.ainvoke(initial_state))

            logger.info(
                "content_generation_execution_completed",
                task_id=self.request.id,
                execution_id=execution_id,
                session_id=session_id,
                generated_content_keys=list(final_state.keys()),
            )
        except Exception as exec_error:
            logger.error(
                "graph_execution_failed",
                execution_id=execution_id,
                target=target,
                error=str(exec_error),
                error_type=type(exec_error).__name__,
                exc_info=True,
            )
            raise
        finally:
            loop.close()

        # Update session's available content identifiers
        # Extract step identifiers from final state (other keys are execution context)
        step_identifiers = [
            k
            for k in final_state.keys()
            if k not in ["session_id", "execution_id", "transcription"]
        ]

        for step_id in step_identifiers:
            if step_id in final_state and final_state[step_id]:
                session_crud.add_available_content_identifier(db, session_id, step_id)
                logger.debug(
                    "content_identifier_ensured_in_session",
                    session_id=session_id,
                    step_id=step_id,
                )

        # Get created content IDs for logging
        created_content = content_crud.get_content_list(db, session_id)
        created_ids = [
            c.id for c in created_content if c.workflow_execution_id == execution_id
        ]

        logger.info(
            "created_content_retrieved_for_logging",
            task_id=self.request.id,
            execution_id=execution_id,
            session_id=session_id,
            created_ids=created_ids,
        )

        # Mark as completed
        WorkflowExecutionService.mark_completed(execution_id, db, created_ids)

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
        # Enhanced error logging with full context
        import traceback

        tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
        tb_str = "".join(tb_lines)

        # Determine if this is a transient error worth retrying (whitelist approach)
        should_retry = _is_transient_error(e)

        logger.error(
            "content_generation_task_failed",
            task_id=self.request.id,
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

        # Only retry on transient errors (whitelist pattern)
        if should_retry:
            try:
                logger.info(
                    "content_generation_task_retrying",
                    task_id=self.request.id,
                    retry_count=self.request.retries,
                    max_retries=self.max_retries,
                    countdown_seconds=60 * (self.request.retries + 1),
                    error_type=type(e).__name__,
                )
                raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))
            except Exception as retry_exception:
                # Max retries exceeded
                logger.error(
                    "content_generation_task_max_retries_exceeded",
                    task_id=self.request.id,
                    execution_id=execution_id,
                    final_error=str(retry_exception),
                )
                pass
        else:
            logger.error(
                "content_generation_task_not_retried_programming_error",
                task_id=self.request.id,
                execution_id=execution_id,
                error_type=type(e).__name__,
                reason="Error is not transient (programming error). Permanent failure recorded.",
            )

    finally:
        if db:
            db.close()
            logger.debug(
                "database_session_closed",
                execution_id=execution_id,
            )


@app.task(
    name="app.async_jobs.tasks.generate_session_embedding",
    bind=True,
    max_retries=2,
    queue="embeds",
)
def generate_session_embedding(
    self,
    session_id: int,
    embedding_text: Optional[str] = None,
):
    """
    Generate and store embedding for a session asynchronously.

    Delegates to generic generate_embedding task.

    Args:
        session_id: Session ID to embed
        embedding_text: Pre-computed text to embed (if None, will fetch from session)
    """
    return generate_embedding.apply_async(
        args=[ENTITY_TYPE_SESSION, session_id],
        kwargs={"embedding_text": embedding_text},
        queue="embeds",
    )


@app.task(
    name="app.async_jobs.tasks.generate_event_embedding",
    bind=True,
    max_retries=2,
    queue="embeds",
)
def generate_event_embedding(
    self,
    event_id: int,
    embedding_text: Optional[str] = None,
):
    """
    Generate and store embedding for an event asynchronously.

    Delegates to generic generate_embedding task.

    Args:
        event_id: Event ID to embed
        embedding_text: Pre-computed text to embed (if None, will fetch from event)
    """
    return generate_embedding.apply_async(
        args=[ENTITY_TYPE_EVENT, event_id],
        kwargs={"embedding_text": embedding_text},
        queue="embeds",
    )


# Health check task (unchanged)
@app.task(name="app.async_jobs.tasks.health_check")
def health_check():
    """Health check task for Celery worker."""
    logger.info(
        "celery_health_check_executed",
        status="ok",
        worker_name=(
            health_check.request.hostname
            if hasattr(health_check, "request")
            else "unknown"
        ),
    )
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
