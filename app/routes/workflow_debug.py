"""Debug endpoints for workflow execution troubleshooting."""

from typing import Any, Dict, List

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK, HTTP_404_NOT_FOUND

from app.async_jobs.celery_app import app as celery_app
from app.async_jobs.tasks import health_check
from app.crud import generated_content as content_crud
from app.database.connection import get_db

logger = structlog.get_logger()

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/workflow-executions", response_model=List[Dict[str, Any]])
async def list_workflow_executions(
    session_id: int = Query(None, description="Filter by session ID"),
    limit: int = Query(50, ge=1, le=500, description="Maximum records to return"),
    db: Session = Depends(get_db),
):
    """
    List all workflow executions for debugging.

    Returns detailed info about workflow execution attempts including:
    - Workflow execution ID
    - Celery task IDs (assigned and actual)
    - Status and error messages
    - Timing information
    """
    logger.info("workflow_executions_debug_requested", session_id=session_id, limit=limit)

    if session_id:
        executions = content_crud.get_workflow_executions_for_session(db, session_id)
    else:
        # Get recent executions across all sessions
        from sqlalchemy import desc

        from app.database.models import WorkflowExecution

        executions = (
            db.query(WorkflowExecution)
            .order_by(desc(WorkflowExecution.created_at))
            .limit(limit)
            .all()
        )

    result = []
    for exec in executions:
        result.append(
            {
                "id": exec.id,
                "session_id": exec.session_id,
                "target": exec.target,
                "status": exec.status,
                "triggered_by": exec.triggered_by,
                "created_at": exec.created_at.isoformat() if exec.created_at else None,
                "completed_at": exec.completed_at.isoformat() if exec.completed_at else None,
                "celery_task_id": exec.celery_task_id,
                "error": exec.error,
            }
        )

    return result


@router.get("/workflow-execution/{execution_id}")
async def get_workflow_execution_debug(
    execution_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed debug info for a specific workflow execution.
    """
    logger.info("workflow_execution_debug_requested", execution_id=execution_id)

    execution = content_crud.get_workflow_execution(db, execution_id)
    if not execution:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Workflow execution {execution_id} not found",
        )

    # Try to get Celery task status if we have a task ID
    celery_task_status = None
    celery_task_info = None
    if execution.celery_task_id:
        try:
            celery_task = celery_app.AsyncResult(execution.celery_task_id)
            celery_task_status = celery_task.state
            celery_task_info = {
                "state": celery_task.state,
                "result": (
                    celery_task.result if celery_task.state in ("SUCCESS", "FAILURE") else None
                ),
                "traceback": celery_task.traceback if celery_task.state == "FAILURE" else None,
            }
            logger.info(
                "celery_task_status_retrieved",
                execution_id=execution_id,
                celery_task_id=execution.celery_task_id,
                task_state=celery_task_status,
            )
        except Exception as e:
            logger.error(
                "failed_to_retrieve_celery_task_status",
                execution_id=execution_id,
                celery_task_id=execution.celery_task_id,
                error=str(e),
            )
            celery_task_info = {"error": str(e)}

    return {
        "id": execution.id,
        "session_id": execution.session_id,
        "target": execution.target,
        "status": execution.status,
        "triggered_by": execution.triggered_by,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        "celery_task_id": execution.celery_task_id,
        "celery_task_status": celery_task_status,
        "celery_task_info": celery_task_info,
        "error": execution.error,
    }


@router.get("/celery-health")
async def celery_health_check():
    """
    Check Celery worker and broker health.

    This endpoint:
    1. Checks if Celery broker is reachable
    2. Sends a test task to the health_check queue
    3. Returns broker and worker connectivity status
    """
    logger.info("celery_health_check_requested")

    broker_available = False
    worker_available = False
    health_task = None

    try:
        # Check broker connection
        from kombu import Connection

        broker_url = celery_app.conf.broker_url
        connection = Connection(broker_url)
        connection.connect()
        connection.close()
        broker_available = True
        logger.info("celery_broker_connection_ok", broker_url=broker_url)
    except Exception as e:
        logger.error(
            "celery_broker_connection_failed",
            broker_url=celery_app.conf.broker_url,
            error=str(e),
        )
        broker_available = False

    # Try to send a health check task
    try:
        health_task = health_check.delay()
        worker_available = True
        logger.info("celery_health_task_queued", task_id=health_task.id)
    except Exception as e:
        logger.error(
            "celery_health_task_failed_to_queue",
            error=str(e),
        )
        worker_available = False

    return {
        "broker_available": broker_available,
        "broker_url": celery_app.conf.broker_url,
        "worker_available": worker_available,
        "health_task_id": health_task.id if health_task else None,
        "status": "healthy" if (broker_available and worker_available) else "degraded",
    }
