"""API routes for Session CRUD management (core resource)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)

from app.crud.event import event_crud
from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import Session as SessionModel
from app.database.models import User
from app.schemas.session import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
    SessionWithEvent,
)
from app.security.auth import (
    can_access_session_content,
    get_current_user,
    get_current_user_optional,
    require_session_owner,
)

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
    - **tags**: List of tags (optional)
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
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: you do not own this event",
            )

    db_session = session_crud.create(db, session_in, owner_id=current_user.id)
    return db_session


@router.get("/{session_id}", response_model=SessionWithEvent)
async def get_session(
    session_id: int,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get a session by ID. Published sessions are public; drafts visible only to owner."""
    db_session = session_crud.read(db, session_id)
    if not db_session:
        logger.warning("session_not_found", session_id=session_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Check access based on publication status
    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "session_access_denied",
            session_id=session_id,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return db_session


@router.get("/by-uri/{uri}", response_model=SessionWithEvent)
async def get_session_by_uri(
    uri: str,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get a session by URI. Published sessions are public; drafts visible only to owner."""
    db_session = session_crud.read_by_uri(db, uri)
    if not db_session:
        logger.warning("session_not_found_by_uri", uri=uri)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Check access based on publication status
    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "session_access_denied_by_uri",
            uri=uri,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return db_session


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    status: str = Query(None, description="Filter by status (draft, published)"),
    event_id: int = Query(None, description="Filter by event ID"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    List all sessions with optional filtering and pagination.

    Public users see only published sessions. Authenticated users also see their own drafts.

    - **skip**: Number of records to skip (default: 0)
    - **limit**: Maximum records to return (default: 100, max: 1000)
    - **status**: Filter by status (optional, e.g., "published" for published sessions only)
    - **event_id**: Filter by event ID (optional)
    """
    if event_id:
        sessions = session_crud.list_by_event(db, event_id, skip=skip, limit=limit)
    elif status:
        sessions = session_crud.list_by_status(db, status, skip=skip, limit=limit)
    else:
        sessions = session_crud.list_all(db, skip=skip, limit=limit)

    # Filter results: only include published sessions or user's own drafts
    filtered_sessions = [s for s in sessions if can_access_session_content(s, current_user)]

    return filtered_sessions


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
            logger.warning("event_not_found_for_session_update", event_id=session_in.event_id)
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Event with ID {session_in.event_id} not found",
            )

    updated_session = session_crud.update(db, session_id, session_in)
    return updated_session


@router.delete("/{session_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Delete a session (owner only)."""
    session_crud.delete(db, session_id)
    return None


@router.get("/event/{event_id}/sessions", response_model=list[SessionResponse])
async def list_event_sessions(
    event_id: int,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    List all sessions for a specific event.

    Public users see only published sessions. Authenticated users also see their own drafts.
    """
    # Verify event exists
    event = event_crud.read(db, event_id)
    if not event:
        logger.warning("event_not_found_for_session_list", event_id=event_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")

    sessions = session_crud.list_by_event(db, event_id, skip=skip, limit=limit)

    # Filter results: only include published sessions or user's own drafts
    filtered_sessions = [s for s in sessions if can_access_session_content(s, current_user)]

    return filtered_sessions
