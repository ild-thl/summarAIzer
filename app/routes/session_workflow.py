"""API routes for Session Workflow Management (sub-resource)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_202_ACCEPTED,
    HTTP_404_NOT_FOUND,
)

from app.crud import generated_content as content_crud
from app.database.connection import get_db
from app.database.models import Session as SessionModel
from app.database.models import User
from app.schemas.content import (
    GeneratedContentListItem,
    WorkflowExecutionListItem,
    WorkflowExecutionOverviewResponse,
    WorkflowExecutionResponse,
    WorkflowStatusResponse,
)
from app.security.auth import get_current_user, require_session_owner

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["session-workflow"])


def _serialize_execution(
    workflow_exec,
    created_content_by_execution: dict[int, list[GeneratedContentListItem]],
) -> WorkflowExecutionListItem:
    return WorkflowExecutionListItem(
        execution_id=workflow_exec.id,
        session_id=workflow_exec.session_id,
        workflow_type=workflow_exec.target,
        status=workflow_exec.status,
        triggered_by=workflow_exec.triggered_by,
        created_at=workflow_exec.created_at,
        started_at=workflow_exec.started_at,
        completed_at=workflow_exec.completed_at,
        celery_task_id=workflow_exec.celery_task_id,
        error_message=workflow_exec.error,
        created_content=created_content_by_execution.get(workflow_exec.id, []),
    )


@router.get(
    "/{session_id}/workflows",
    response_model=WorkflowExecutionOverviewResponse,
)
async def list_workflow_executions(
    session_id: int,
    history_limit: int = 20,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """List active and historical workflow executions for a session (owner only)."""
    from app.database.models import WorkflowExecutionStatus

    bounded_history_limit = max(1, min(history_limit, 100))
    executions = content_crud.get_workflow_executions_for_session(db, session_id)

    execution_ids = [exec_item.id for exec_item in executions]
    created_content_by_execution: dict[int, list[GeneratedContentListItem]] = {
        execution_id: [] for execution_id in execution_ids
    }

    if execution_ids:
        contents = content_crud.list_for_session(db, session_id)
        for content in contents:
            if content.workflow_execution_id in created_content_by_execution:
                created_content_by_execution[content.workflow_execution_id].append(
                    GeneratedContentListItem(
                        id=content.id,
                        identifier=content.identifier,
                        content_type=content.content_type,
                        workflow_execution_id=content.workflow_execution_id,
                        created_at=content.created_at,
                        created_by_user_id=content.created_by_user_id,
                    )
                )

    running_statuses = {
        WorkflowExecutionStatus.QUEUED,
        WorkflowExecutionStatus.RUNNING,
        WorkflowExecutionStatus.QUEUED.value,
        WorkflowExecutionStatus.RUNNING.value,
    }

    running_items = []
    history_items = []
    for exec_item in executions:
        serialized = _serialize_execution(exec_item, created_content_by_execution)
        if exec_item.status in running_statuses:
            running_items.append(serialized)
            continue

        history_items.append(serialized)

    return WorkflowExecutionOverviewResponse(
        running=running_items,
        history=history_items[:bounded_history_limit],
        has_running=len(running_items) > 0,
    )


@router.post(
    "/{session_id}/workflow/{workflow_type}",
    response_model=WorkflowExecutionResponse,
    status_code=HTTP_202_ACCEPTED,
)
async def trigger_workflow(
    session_id: int,
    workflow_type: str,
    _session: SessionModel = Depends(require_session_owner),
    current_user: User = Depends(get_current_user),
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
        from app.services.execution_service import WorkflowExecutionService

        # Service layer handles validation, resolving, and queuing
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=session_id,
            target=workflow_type,
            db=db,
            triggered_by="user_triggered",
            created_by_user_id=current_user.id,
        )

        logger.info(
            "workflow_triggered",
            session_id=session_id,
            workflow_type=workflow_type,
            workflow_execution_id=workflow_exec.id,
            celery_task_id=celery_task_id,
            triggered_by_user_id=current_user.id,
        )

        return WorkflowExecutionResponse(
            task_id=str(workflow_exec.id),
            execution_id=workflow_exec.id,
            session_id=session_id,
            workflow_type=workflow_type,
            status="queued",
            created_at=workflow_exec.created_at,
            started_at=workflow_exec.started_at,
            completed_at=workflow_exec.completed_at,
            celery_task_id=celery_task_id,
        )

    except ValueError as e:
        logger.warning(
            "workflow_trigger_error",
            session_id=session_id,
            workflow_type=workflow_type,
            error=str(e),
        )
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/{session_id}/workflow/{execution_id}",
    response_model=WorkflowStatusResponse,
)
async def get_workflow_status(
    session_id: int,
    execution_id: int,
    _: SessionModel = Depends(require_session_owner),
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
    from app.services.execution_service import WorkflowExecutionService

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
        contents = content_crud.list_for_session(db, session_id)
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
        execution_id=workflow_exec.id,
        session_id=workflow_exec.session_id,
        workflow_type=workflow_exec.target,
        status=workflow_exec.status,
        created_at=workflow_exec.created_at,
        started_at=workflow_exec.started_at,
        completed_at=workflow_exec.completed_at,
        celery_task_id=workflow_exec.celery_task_id,
        error_message=workflow_exec.error,
        created_content=created_content,
    )
