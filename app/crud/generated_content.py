"""CRUD operations for generated content."""

import json
from datetime import datetime

import structlog
from sqlalchemy import and_, desc
from sqlalchemy.orm import Session as SQLSession

from app.database.models import GeneratedContent, WorkflowExecution
from app.services.s3_service import get_s3_service

logger = structlog.get_logger()


def _extract_s3_key_from_content(content: str | None) -> str | None:
    """Try to extract an `s3_key` from JSON content or a plain URL.

    Returns the raw s3 key (e.g. "content/summaraizer/...") or None.
    """
    if not content:
        return None

    # Try JSON parse first
    try:
        payload = json.loads(content)
        if isinstance(payload, dict) and payload.get("s3_key"):
            return payload.get("s3_key")
    except Exception:
        pass

    # If content looks like a full public URL, try to strip the base
    if isinstance(content, str) and content.startswith("http"):
        # aws_url configured in services will include the base; use generic S3 service
        try:
            svc = get_s3_service()
            base = (svc.aws_url or "").rstrip("/")
            if base and content.startswith(base):
                return content[len(base) + 1 :]
        except Exception:
            pass

    return None


def _delete_s3_for_generated_content(db_content: GeneratedContent) -> None:
    """Delete any S3 objects referenced by a GeneratedContent record.

    Non-fatal: log and continue on errors.
    """
    try:
        s3_key = _extract_s3_key_from_content(db_content.content)
        if not s3_key:
            # Try meta_info fallback
            meta = getattr(db_content, "meta_info", None) or {}
            s3_key = meta.get("s3_key") or meta.get("image_url")
            if isinstance(s3_key, str) and s3_key.startswith("http"):
                # convert image_url to key if possible
                s3_key = _extract_s3_key_from_content(s3_key)

        if not s3_key:
            return

        # Use the generic S3 service to delete the referenced object.
        svc = get_s3_service()
        try:
            svc.delete_object(s3_key)
            logger.info("deleted_s3_object", s3_key=s3_key, session_id=db_content.session_id)
        except Exception:
            logger.exception("failed_delete_s3_object", s3_key=s3_key)
        return

    except Exception:
        logger.exception("s3_cleanup_unexpected_error", content_id=getattr(db_content, "id", None))


def create_content(
    db: SQLSession,
    session_id: int,
    identifier: str,
    content: str,
    content_type: str = "plain_text",
    workflow_execution_id: int | None = None,
    created_by_user_id: int | None = None,
    meta_info: dict | None = None,
    ai_generated: bool | None = None,
    editorially_reviewed: bool = False,
) -> GeneratedContent:
    """Create new generated content record."""
    db_content = GeneratedContent(
        session_id=session_id,
        identifier=identifier,
        content_type=content_type,
        content=content,
        ai_generated=(workflow_execution_id is not None) if ai_generated is None else ai_generated,
        editorially_reviewed=editorially_reviewed,
        workflow_execution_id=workflow_execution_id,
        created_by_user_id=created_by_user_id,
        meta_info=meta_info,
    )
    db.add(db_content)
    db.commit()
    db.refresh(db_content)
    return db_content


def create_or_update_content(
    db: SQLSession,
    session_id: int,
    identifier: str,
    content: str,
    content_type: str = "plain_text",
    workflow_execution_id: int | None = None,
    created_by_user_id: int | None = None,
    meta_info: dict | None = None,
    ai_generated: bool | None = None,
    editorially_reviewed: bool | None = None,
) -> GeneratedContent:
    """
    Create new content or update existing if already exists.

    Handles the case where a workflow is retried and tries to insert
    the same content again. Uses upsert pattern to avoid unique violations.

    Args:
        db: Database session
        session_id: Session ID
        identifier: Content identifier (step name)
        content: Content data
        content_type: Type of content
        workflow_execution_id: Workflow execution ID (used in unique constraint)
        created_by_user_id: User who created (optional)
        meta_info: Metadata dict (optional)

    Returns:
        GeneratedContent record (new or updated)
    """
    # Check if content already exists
    existing = (
        db.query(GeneratedContent)
        .filter(
            and_(
                GeneratedContent.session_id == session_id,
                GeneratedContent.identifier == identifier,
            )
        )
        .first()
    )

    if existing:
        try:
            old_s3_key = _extract_s3_key_from_content(existing.content)
            new_s3_key = _extract_s3_key_from_content(content)
            if old_s3_key and old_s3_key != new_s3_key:
                _delete_s3_for_generated_content(existing)
        except Exception:
            logger.exception("s3_cleanup_before_update_failed", content_id=existing.id)

        # Update existing record instead of creating duplicate
        existing.content = content
        existing.content_type = content_type
        existing.meta_info = meta_info
        if ai_generated is not None:
            existing.ai_generated = ai_generated
        elif workflow_execution_id is not None:
            existing.ai_generated = True
        if editorially_reviewed is not None:
            existing.editorially_reviewed = editorially_reviewed
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        # Create new record if doesn't exist
        return create_content(
            db=db,
            session_id=session_id,
            identifier=identifier,
            content=content,
            content_type=content_type,
            workflow_execution_id=workflow_execution_id,
            created_by_user_id=created_by_user_id,
            meta_info=meta_info,
            ai_generated=ai_generated,
            editorially_reviewed=editorially_reviewed or False,
        )


def get_content_by_id(db: SQLSession, content_id: int) -> GeneratedContent | None:
    """Get content by ID."""
    return db.query(GeneratedContent).filter(GeneratedContent.id == content_id).first()


def get_content_by_identifier(
    db: SQLSession, session_id: int, identifier: str
) -> GeneratedContent | None:
    """Get latest generated content for a session and identifier."""
    return (
        db.query(GeneratedContent)
        .filter(
            and_(
                GeneratedContent.session_id == session_id,
                GeneratedContent.identifier == identifier,
            )
        )
        .order_by(desc(GeneratedContent.created_at))
        .first()
    )


def list_for_session(
    db: SQLSession, session_id: int, identifier: str | None = None
) -> list[GeneratedContent]:
    """
    List all generated content for a session, ordered by creation time.

    Optionally filtered by identifier.

    Used by documentation builder to assemble published artifacts.
    """
    query = db.query(GeneratedContent).filter(GeneratedContent.session_id == session_id)

    if identifier:
        query = query.filter(GeneratedContent.identifier == identifier)

    return query.order_by(GeneratedContent.created_at.asc()).all()


def list_content_identifiers(db: SQLSession, session_id: int) -> list[str]:
    """Get list of available identifiers for session."""
    contents = (
        db.query(GeneratedContent.identifier)
        .filter(GeneratedContent.session_id == session_id)
        .distinct()
        .all()
    )
    return [c[0] for c in contents]


def update_content(
    db: SQLSession,
    content_id: int,
    content: str,
    meta_info: dict | None = None,
    editorially_reviewed: bool | None = None,
) -> GeneratedContent | None:
    """Update content (for manual edits)."""
    db_content = get_content_by_id(db, content_id)
    if db_content:
        # If updating content, attempt to delete any previous S3 assets
        try:
            old_s3_key = _extract_s3_key_from_content(db_content.content)
            new_s3_key = _extract_s3_key_from_content(content)
            if old_s3_key and old_s3_key != new_s3_key:
                _delete_s3_for_generated_content(db_content)
        except Exception:
            logger.exception("s3_cleanup_before_manual_update_failed", content_id=db_content.id)

        db_content.content = content
        if meta_info is not None:
            db_content.meta_info = meta_info
        if editorially_reviewed is not None:
            db_content.editorially_reviewed = editorially_reviewed
        db_content.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(db_content)
    return db_content


def update_content_editorial_review(
    db: SQLSession,
    content_id: int,
    editorially_reviewed: bool,
) -> GeneratedContent | None:
    """Update editorial review flag for a content record."""
    db_content = get_content_by_id(db, content_id)
    if db_content:
        db_content.editorially_reviewed = editorially_reviewed
        db_content.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(db_content)
    return db_content


def delete_content(db: SQLSession, content_id: int) -> bool:
    """Delete content by ID."""
    db_content = get_content_by_id(db, content_id)
    if db_content:
        db.delete(db_content)
        db.commit()
        return True
    return False


def delete_content_by_identifier(db: SQLSession, session_id: int, identifier: str) -> bool:
    """Delete all content with given identifier for session."""
    # Fetch affected rows first so we can clean up any referenced S3 objects
    contents = (
        db.query(GeneratedContent)
        .filter(
            and_(
                GeneratedContent.session_id == session_id,
                GeneratedContent.identifier == identifier,
            )
        )
        .all()
    )

    for c in contents:
        try:
            _delete_s3_for_generated_content(c)
        except Exception:
            logger.exception("s3_cleanup_failed_before_delete", content_id=c.id)

    count = (
        db.query(GeneratedContent)
        .filter(
            and_(
                GeneratedContent.session_id == session_id,
                GeneratedContent.identifier == identifier,
            )
        )
        .delete()
    )
    db.commit()
    return count > 0


# WorkflowExecution CRUD


def create_workflow_execution(
    db: SQLSession,
    session_id: int,
    target: str,
    triggered_by: str = "user_triggered",
    created_by_user_id: int | None = None,
    celery_task_id: str | None = None,
) -> WorkflowExecution:
    """Create new workflow execution record."""
    from app.database.models import WorkflowExecutionStatus

    db_exec = WorkflowExecution(
        session_id=session_id,
        target=target,
        status=WorkflowExecutionStatus.QUEUED,
        triggered_by=triggered_by,
        created_by_user_id=created_by_user_id,
        celery_task_id=celery_task_id,
    )
    db.add(db_exec)
    db.commit()
    db.refresh(db_exec)
    return db_exec


def get_workflow_execution(db: SQLSession, execution_id: int) -> WorkflowExecution | None:
    """Get workflow execution by ID."""
    return db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()


def get_workflow_execution_by_task_id(db: SQLSession, task_id: str) -> WorkflowExecution | None:
    """Get workflow execution by Celery task ID."""
    return db.query(WorkflowExecution).filter(WorkflowExecution.celery_task_id == task_id).first()


def update_workflow_status(
    db: SQLSession,
    execution_id: int,
    status: str,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> WorkflowExecution | None:
    """Update workflow execution status."""
    db_exec = get_workflow_execution(db, execution_id)
    if db_exec:
        db_exec.status = status
        if error_message is not None:
            db_exec.error_message = error_message
        if completed_at is not None:
            db_exec.completed_at = completed_at
        db.commit()
        db.refresh(db_exec)
    return db_exec


def get_workflow_executions_for_session(db: SQLSession, session_id: int) -> list[WorkflowExecution]:
    """Get all workflow executions for a session."""
    return (
        db.query(WorkflowExecution)
        .filter(WorkflowExecution.session_id == session_id)
        .order_by(desc(WorkflowExecution.created_at))
        .all()
    )
