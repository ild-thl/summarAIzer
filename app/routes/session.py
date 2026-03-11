"""API routes for Session management."""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)
from app.database.connection import get_db
from app.schemas.session import (
    SessionCreate,
    SessionUpdate,
    SessionResponse,
    SessionWithEvent,
)
from app.schemas.content import (
    GeneratedContentCreate,
    GeneratedContentUpdate,
    GeneratedContentResponse,
    GeneratedContentListItem,
    WorkflowExecutionResponse,
    WorkflowStatusResponse,
    SessionContentListResponse,
)
from app.crud.session import session_crud
from app.crud.event import event_crud
from app.crud import generated_content as content_crud
from app.database.models import SessionStatus, User, Session as SessionModel
from app.security.auth import get_current_user, require_session_owner
import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=HTTP_201_CREATED)
async def create_session(
    session_in: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new session (requires authentication).

    - **title**: Session title (required)
    - **uri**: URL-safe identifier (required, must be unique)
    - **start_datetime**: Session start datetime (required)
    - **end_datetime**: Session end datetime (required, must be after start_datetime)
    - **speakers**: List of speakers (optional)
    - **categories**: List of categories (optional)
    - **short_description**: Short description (optional)
    - **location**: Session location (optional)
    - **recording_url**: Recording URL (optional)
    - **status**: Session status - draft or published (default: draft)
    - **session_format**: Format like Input, Lighting Talk, Diskussion, workshop, Training (optional)
    - **duration**: Duration in minutes (optional, or auto-calculated from times)
    - **language**: ISO 639-1 language code (default: en)
    - **event_id**: Associated event ID (optional)
    """
    # Check if URI already exists
    existing = session_crud.read_by_uri(db, session_in.uri)
    if existing:
        logger.warning("session_uri_conflict", uri=session_in.uri)
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"Session with URI '{session_in.uri}' already exists",
        )

    # Validate event exists and user owns it (if event_id provided)
    if session_in.event_id:
        event = event_crud.read(db, session_in.event_id)
        if not event:
            logger.warning("event_not_found_for_session", event_id=session_in.event_id)
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Event with ID {session_in.event_id} not found",
            )
        if event.owner_id != current_user.id:
            logger.warning(
                "auth_unauthorized_event_access_for_session",
                user_id=current_user.id,
                event_id=session_in.event_id,
            )
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Permission denied: you do not own this event",
            )

    db_session = session_crud.create(db, session_in, owner_id=current_user.id)
    return db_session


@router.get("/{session_id}", response_model=SessionWithEvent)
async def get_session(session_id: int, db: Session = Depends(get_db)):
    """Get a session by ID."""
    db_session = session_crud.read(db, session_id)
    if not db_session:
        logger.warning("session_not_found", session_id=session_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")
    return db_session


@router.get("/by-uri/{uri}", response_model=SessionWithEvent)
async def get_session_by_uri(uri: str, db: Session = Depends(get_db)):
    """Get a session by URI."""
    db_session = session_crud.read_by_uri(db, uri)
    if not db_session:
        logger.warning("session_not_found_by_uri", uri=uri)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")
    return db_session


@router.get("", response_model=List[SessionResponse])
async def list_sessions(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    status: str = Query(None, description="Filter by status (draft, published)"),
    event_id: int = Query(None, description="Filter by event ID"),
    published_only: bool = Query(False, description="Return only published sessions"),
    db: Session = Depends(get_db),
):
    """
    List all sessions with optional filtering and pagination.

    - **skip**: Number of records to skip (default: 0)
    - **limit**: Maximum records to return (default: 100, max: 1000)
    - **status**: Filter by status (optional)
    - **event_id**: Filter by event ID (optional)
    - **published_only**: Return only published sessions (optional)
    """
    if published_only:
        sessions = session_crud.list_published(db, skip=skip, limit=limit)
    elif event_id:
        sessions = session_crud.list_by_event(db, event_id, skip=skip, limit=limit)
    elif status:
        sessions = session_crud.list_by_status(db, status, skip=skip, limit=limit)
    else:
        sessions = session_crud.list_all(db, skip=skip, limit=limit)
    return sessions


@router.patch("/{session_id}", response_model=SessionWithEvent)
async def update_session(
    session_id: int,
    session_in: SessionUpdate,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Update a session partially (owner only).

    Only provide fields that need to be updated.
    """
    # Check URI conflict if URI is being updated
    if session_in.uri and session_in.uri != session.uri:
        existing = session_crud.read_by_uri(db, session_in.uri)
        if existing:
            logger.warning(
                "session_uri_conflict_on_update",
                session_id=session_id,
                uri=session_in.uri,
            )
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=f"Session with URI '{session_in.uri}' already exists",
            )

    # Validate event exists if event_id is being updated
    if session_in.event_id and session_in.event_id != session.event_id:
        event = event_crud.read(db, session_in.event_id)
        if not event:
            logger.warning(
                "event_not_found_for_session_update", event_id=session_in.event_id
            )
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Event with ID {session_in.event_id} not found",
            )

    updated_session = session_crud.update(db, session_id, session_in)
    return updated_session


@router.delete("/{session_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Delete a session (owner only)."""
    session_crud.delete(db, session_id)
    return None


@router.get("/event/{event_id}/sessions", response_model=List[SessionResponse])
async def list_event_sessions(
    event_id: int,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    db: Session = Depends(get_db),
):
    """List all sessions for a specific event."""
    # Verify event exists
    event = event_crud.read(db, event_id)
    if not event:
        logger.warning("event_not_found_for_session_list", event_id=event_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")

    sessions = session_crud.list_by_event(db, event_id, skip=skip, limit=limit)
    return sessions


# Content Management Endpoints


@router.get(
    "/{session_id}/content", response_model=SessionContentListResponse
)
async def get_available_content(session_id: int, db: Session = Depends(get_db)):
    """Get list of available content identifiers for session."""
    db_session = session_crud.read(db, session_id)
    if not db_session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return SessionContentListResponse(
        available_content=db_session.available_content_identifiers
    )


@router.post(
    "/{session_id}/content/transcription",
    response_model=GeneratedContentResponse,
    status_code=HTTP_201_CREATED,
)
async def add_transcription(
    session_id: int,
    content_in: GeneratedContentCreate,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Add transcription content to session (owner only).
    
    This creates a GeneratedContent record with identifier="transcription"
    and workflow_execution_id=NULL to indicate it was manually provided.
    """
    # Check if transcription already exists
    existing_tx = content_crud.get_content_by_identifier(db, session_id, "transcription")
    if existing_tx:
        logger.warning("transcription_already_exists", session_id=session_id)
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail="Transcription already exists for this session. Delete and re-create to update.",
        )

    # Create transcription content
    db_content = content_crud.create_content(
        db=db,
        session_id=session_id,
        identifier="transcription",
        content=content_in.content,
        content_type="plain_text",
        workflow_execution_id=None,  # Manually provided
        meta_info=content_in.meta_info,
    )

    # Update session's available content
    session_crud.add_available_content_identifier(db, session_id, "transcription")

    logger.info(
        "transcription_added",
        session_id=session_id,
        content_id=db_content.id,
    )

    return db_content


@router.get(
    "/{session_id}/content/{identifier}",
    response_model=GeneratedContentResponse,
)
async def get_content_by_identifier(
    session_id: int,
    identifier: str,
    db: Session = Depends(get_db),
):
    """Retrieve latest generated content for a session and identifier."""
    db_session = session_crud.read(db, session_id)
    if not db_session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    db_content = content_crud.get_content_by_identifier(db, session_id, identifier)
    if not db_content:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Content with identifier '{identifier}' not found for this session",
        )

    return db_content


@router.patch(
    "/{session_id}/content/{identifier}",
    response_model=GeneratedContentResponse,
)
async def update_content(
    session_id: int,
    identifier: str,
    content_in: GeneratedContentUpdate,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Update generated content (owner only, e.g., manual edits after generation)."""
    db_content = content_crud.get_content_by_identifier(db, session_id, identifier)
    if not db_content:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Content with identifier '{identifier}' not found",
        )

    updated = content_crud.update_content(
        db=db,
        content_id=db_content.id,
        content=content_in.content,
        meta_info=content_in.meta_info,
    )

    logger.info(
        "content_updated",
        session_id=session_id,
        identifier=identifier,
        content_id=db_content.id,
    )

    return updated


@router.delete(
    "/{session_id}/content/{identifier}",
    status_code=HTTP_204_NO_CONTENT,
)
async def delete_content(
    session_id: int,
    identifier: str,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Delete content with specific identifier (owner only)."""
    if not content_crud.delete_content_by_identifier(db, session_id, identifier):
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Content with identifier '{identifier}' not found",
        )

    # Remove from session's available content
    session_crud.remove_available_content_identifier(db, session_id, identifier)

    logger.info(
        "content_deleted",
        session_id=session_id,
        identifier=identifier,
    )

    return None


# Workflow Endpoints


@router.post(
    "/{session_id}/workflow",
    response_model=WorkflowExecutionResponse,
    status_code=HTTP_202_ACCEPTED,
)
async def trigger_workflow(
    session_id: int,
    target: str = Query("talk_workflow", description="Workflow target: 'talk_workflow' for all steps, or individual step name"),
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Trigger content generation workflow for a session (owner only).
    
    Returns immediately with execution ID. Use GET /{session_id}/workflow/{execution_id} to check status.
    
    **target** options (query parameter):
    - `talk_workflow` - Generate all content (default): summary, tags, takeaways, diagram, image
    - Individual steps: `summary`, `tags`, `key_takeaways`, `mermaid`, `image`
    """
    try:
        # Lazy import to avoid circular imports
        from app.workflows.services.execution_service import WorkflowExecutionService
        
        # Get current user from session dependency (already validated ownership)
        current_user = (
            db.query(User)
            .filter(User.id == session.owner_id)
            .first()
        )
        
        # Service layer handles validation, resolving, and queuing
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=session_id,
            target=target,
            db=db,
            triggered_by="user_triggered",
            created_by_user_id=current_user.id if current_user else None,
        )
        
        logger.info(
            "workflow_triggered",
            session_id=session_id,
            target=target,
            workflow_execution_id=workflow_exec.id,
            celery_task_id=celery_task_id,
            triggered_by_user_id=current_user.id if current_user else None,
        )
        
        return WorkflowExecutionResponse(
            task_id=str(workflow_exec.id),
            workflow_type=target,
            status="queued",
            created_at=workflow_exec.created_at,
        )
    
    except ValueError as e:
        logger.warning(
            "workflow_trigger_error",
            session_id=session_id,
            target=target,
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
    db: Session = Depends(get_db),
):
    """
    Check workflow execution status.
    
    Returns current status and created content when completed.
    """
    # Validate session exists
    db_session = session_crud.read(db, session_id)
    if not db_session:
        logger.warning(
            "workflow_status_session_not_found",
            session_id=session_id,
            execution_id=execution_id,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")
    
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
