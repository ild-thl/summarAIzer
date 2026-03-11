"""CRUD operations for generated content."""

from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session as SQLSession
from sqlalchemy import desc, and_

from app.database.models import GeneratedContent, WorkflowExecution


def create_content(
    db: SQLSession,
    session_id: int,
    identifier: str,
    content: str,
    content_type: str = "plain_text",
    workflow_execution_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    meta_info: Optional[dict] = None,
) -> GeneratedContent:
    """Create new generated content record."""
    db_content = GeneratedContent(
        session_id=session_id,
        identifier=identifier,
        content_type=content_type,
        content=content,
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
    workflow_execution_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    meta_info: Optional[dict] = None,
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
                GeneratedContent.workflow_execution_id == workflow_execution_id,
            )
        )
        .first()
    )
    
    if existing:
        # Update existing record instead of creating duplicate
        existing.content = content
        existing.content_type = content_type
        existing.meta_info = meta_info
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
        )


def get_content_by_id(db: SQLSession, content_id: int) -> Optional[GeneratedContent]:
    """Get content by ID."""
    return db.query(GeneratedContent).filter(GeneratedContent.id == content_id).first()


def get_content_by_identifier(
    db: SQLSession, session_id: int, identifier: str
) -> Optional[GeneratedContent]:
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


def get_content_list(
    db: SQLSession, session_id: int, identifier: Optional[str] = None
) -> List[GeneratedContent]:
    """List content for session, optionally filtered by identifier."""
    query = db.query(GeneratedContent).filter(GeneratedContent.session_id == session_id)
    if identifier:
        query = query.filter(GeneratedContent.identifier == identifier)
    return query.order_by(desc(GeneratedContent.created_at)).all()


def list_content_identifiers(db: SQLSession, session_id: int) -> List[str]:
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
    meta_info: Optional[dict] = None,
) -> Optional[GeneratedContent]:
    """Update content (for manual edits)."""
    db_content = get_content_by_id(db, content_id)
    if db_content:
        db_content.content = content
        if meta_info is not None:
            db_content.meta_info = meta_info
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


def delete_content_by_identifier(
    db: SQLSession, session_id: int, identifier: str
) -> bool:
    """Delete all content with given identifier for session."""
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
    created_by_user_id: Optional[int] = None,
    celery_task_id: Optional[str] = None,
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


def get_workflow_execution(db: SQLSession, execution_id: int) -> Optional[WorkflowExecution]:
    """Get workflow execution by ID."""
    return db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()


def get_workflow_execution_by_task_id(
    db: SQLSession, task_id: str
) -> Optional[WorkflowExecution]:
    """Get workflow execution by Celery task ID."""
    return (
        db.query(WorkflowExecution)
        .filter(WorkflowExecution.celery_task_id == task_id)
        .first()
    )


def update_workflow_status(
    db: SQLSession,
    execution_id: int,
    status: str,
    error_message: Optional[str] = None,
    completed_at: Optional[datetime] = None,
) -> Optional[WorkflowExecution]:
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


def get_workflow_executions_for_session(
    db: SQLSession, session_id: int
) -> List[WorkflowExecution]:
    """Get all workflow executions for a session."""
    return (
        db.query(WorkflowExecution)
        .filter(WorkflowExecution.session_id == session_id)
        .order_by(desc(WorkflowExecution.created_at))
        .all()
    )
