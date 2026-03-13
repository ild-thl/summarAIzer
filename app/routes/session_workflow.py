"""API routes for Session Workflow Management (sub-resource)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_202_ACCEPTED,
    HTTP_404_NOT_FOUND,
)
import structlog

from app.database.connection import get_db
from app.schemas.content import (
    GeneratedContentListItem,
    WorkflowExecutionResponse,
    WorkflowStatusResponse,
)
from app.crud.session import session_crud
from app.crud import generated_content as content_crud
from app.database.models import User, Session as SessionModel
from app.security.auth import require_session_owner

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["session-workflow"])


@router.post(
    "/{session_id}/workflow/{workflow_type}",
    response_model=WorkflowExecutionResponse,
    status_code=HTTP_202_ACCEPTED,
)
async def trigger_workflow(
    session_id: int,
    workflow_type: str,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Trigger content generation workflow for a session (owner only).

    Returns immediately with execution ID. Use GET /{session_id}/workflow/{execution_id} to check status.

    **workflow_type** options (path parameter):
    - `talk_workflow` - Generate all content: summary, tags, takeaways, diagram, image
    - Individual steps: `summary`, `tags`, `key_takeaways`, `mermaid`, `image`
    """
    try:
        # Lazy import to avoid circular imports
        from app.workflows.services.execution_service import WorkflowExecutionService

        # Get current user from session dependency (already validated ownership)
        current_user = db.query(User).filter(User.id == session.owner_id).first()

        # Service layer handles validation, resolving, and queuing
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=session_id,
            target=workflow_type,
            db=db,
            triggered_by="user_triggered",
            created_by_user_id=current_user.id if current_user else None,
        )

        logger.info(
            "workflow_triggered",
            session_id=session_id,
            workflow_type=workflow_type,
            workflow_execution_id=workflow_exec.id,
            celery_task_id=celery_task_id,
            triggered_by_user_id=current_user.id if current_user else None,
        )

        return WorkflowExecutionResponse(
            task_id=str(workflow_exec.id),
            workflow_type=workflow_type,
            status="queued",
            created_at=workflow_exec.created_at,
        )

    except ValueError as e:
        logger.warning(
            "workflow_trigger_error",
            session_id=session_id,
            workflow_type=workflow_type,
            error=str(e),
        )
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{session_id}/workflow/{execution_id}",
    response_model=WorkflowStatusResponse,
)
async def get_workflow_status(
    session_id: int,
    execution_id: int,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Check workflow execution status (owner only).

    Returns current status and created content when completed.
    """
    logger.info(
        "workflow_status_requested",
        session_id=session_id,
        execution_id=execution_id,
    )

    # Lazy import to avoid circular imports
    from app.workflows.services.execution_service import WorkflowExecutionService

    # Get execution by ID
    workflow_exec = WorkflowExecutionService.get_execution_status(execution_id, db)
    if not workflow_exec or workflow_exec.session_id != session_id:
        logger.warning(
            "workflow_execution_not_found",
            session_id=session_id,
            execution_id=execution_id,
        )
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Workflow execution {execution_id} not found for this session",
        )

    # Get generated content if completed
    created_content = []
    if workflow_exec.status == "completed":
        contents = content_crud.get_content_list(db, session_id)
        created_content = [
            GeneratedContentListItem(
                id=c.id,
                identifier=c.identifier,
                content_type=c.content_type,
                workflow_execution_id=c.workflow_execution_id,
                created_at=c.created_at,
                created_by_user_id=c.created_by_user_id,
            )
            for c in contents
            if c.workflow_execution_id == workflow_exec.id
        ]

    logger.info(
        "workflow_status_returned",
        session_id=session_id,
        execution_id=execution_id,
        status=workflow_exec.status,
        created_content_count=len(created_content),
        error=workflow_exec.error,
    )

    return WorkflowStatusResponse(
        status=workflow_exec.status,
        created_at=workflow_exec.created_at,
        completed_at=workflow_exec.completed_at,
        error_message=workflow_exec.error,
        created_content=created_content,
    )
