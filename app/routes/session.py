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
from app.utils.helpers import DateTimeUtils

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


def _validate_and_parse_enum_list(value: str, enum_class, field_name: str) -> list[str] | None:
    """Validate and parse comma-separated enum values."""
    if not value:
        return None

    values_list = [v.strip() for v in value.split(",") if v.strip()]
    valid_values = [e.value for e in enum_class]

    for v in values_list:
        if v not in valid_values:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid {field_name} '{v}'. Allowed values: {', '.join(valid_values)}",
            )

    return values_list


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    status: str = Query(
        None, description="Filter by status - comma-separated (draft, published) - OR logic"
    ),
    event_id: int = Query(None, description="Filter by event ID"),
    session_format: str = Query(
        None,
        description="Filter by session format - comma-separated (Input, Lighting Talk, Diskussion, workshop, Training) - OR logic",
    ),
    tags: str = Query(None, description="Filter by tags (comma-separated, OR logic)"),
    location: str = Query(None, description="Filter by location (comma-separated, OR logic)"),
    language: str = Query(
        None,
        description="Filter by language - comma-separated (ISO 639-1 code, e.g., en,de) - OR logic",
    ),
    duration_min: int = Query(None, ge=0, description="Minimum duration in minutes"),
    duration_max: int = Query(None, ge=0, description="Maximum duration in minutes"),
    speaker: str = Query(None, description="Search for speaker name"),
    start_after: str = Query(None, description="Sessions starting after (ISO 8601)"),
    start_before: str = Query(None, description="Sessions starting before (ISO 8601)"),
    end_after: str = Query(None, description="Sessions ending after (ISO 8601)"),
    end_before: str = Query(None, description="Sessions ending before (ISO 8601)"),
    search: str = Query(None, description="Full-text search on title, description, and speakers"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    List all sessions with advanced filtering and full-text search.

    Public users see only published sessions. Authenticated users also see their own drafts.

    **Filters (all optional):**
    - **skip**: Number of records to skip (default: 0)
    - **limit**: Maximum records to return (default: 100, max: 1000)
    - **status**: Filter by status - comma-separated (draft, published) - OR logic
    - **event_id**: Filter by event ID
    - **session_format**: Filter by session format - comma-separated (Input, Lighting Talk, Diskussion, workshop, Training) - OR logic
    - **tags**: Filter by tags - comma-separated list (OR logic: returns sessions with any tag)
    - **location**: Filter by location - comma-separated list (OR logic: returns sessions with any location)
    - **language**: Filter by language code - comma-separated (e.g., en, de, fr) - OR logic
    - **duration_min**: Minimum duration in minutes
    - **duration_max**: Maximum duration in minutes
    - **speaker**: Search for speaker name (case-insensitive)
    - **start_after**: Sessions starting after date (ISO 8601, e.g., 2024-01-01T00:00:00)
    - **start_before**: Sessions starting before date (ISO 8601)
    - **end_after**: Sessions ending after date (ISO 8601)
    - **end_before**: Sessions ending before date (ISO 8601)
    - **search**: Full-text search on title, description, and speakers (case-insensitive)

    **Examples:**
    - `/api/v2/sessions?status=published&language=en`
    - `/api/v2/sessions?event_id=5&duration_min=20&duration_max=60`
    - `/api/v2/sessions?tags=ai,machine+learning&language=en,de`
    - `/api/v2/sessions?location=Landing:Stage+Berlin,AI:Stage+TU+Graz` (locations with URL encoding)
    - `/api/v2/sessions?tags=AI%26Technology,FutureSkills` (tags with ampersand - URL-encoded)
    - `/api/v2/sessions?search=machine+learning&status=published`
    - `/api/v2/sessions?session_format=input,workshop`
    - `/api/v2/sessions?start_after=2024-06-01T10:00:00&end_before=2024-06-01T11:30:00` (sessions in timeframe)
    """
    from app.database.models import SessionFormat, SessionStatus

    # Validate and parse enum values using helper
    status_list = _validate_and_parse_enum_list(status, SessionStatus, "status") if status else None
    session_format_list = (
        _validate_and_parse_enum_list(session_format, SessionFormat, "session_format")
        if session_format
        else None
    )

    # Parse language (comma-separated, normalize to lowercase for consistency)
    language_list = None
    if language:
        language_list = [lang.strip().lower() for lang in language.split(",") if lang.strip()]

    # Parse location (comma-separated)
    location_list = None
    if location:
        location_list = [loc.strip() for loc in location.split(",") if loc.strip()]

    # Parse datetime strings if provided using helper
    start_after_dt, start_before_dt, end_after_dt, end_before_dt = (
        DateTimeUtils.parse_datetime_filters(start_after, start_before, end_after, end_before)
    )

    # Parse tags (comma-separated)
    tags_list = None
    if tags:
        tags_list = [t.strip() for t in tags.split(",") if t.strip()]

    # Use enhanced filtering
    sessions = session_crud.list_with_filters(
        db,
        skip=skip,
        limit=limit,
        status=status_list,
        event_id=event_id,
        session_format=session_format_list,
        tags=tags_list,
        location=location_list,
        language=language_list,
        duration_min=duration_min,
        duration_max=duration_max,
        speaker=speaker,
        start_after=start_after_dt,
        start_before=start_before_dt,
        end_after=end_after_dt,
        end_before=end_before_dt,
        search=search,
    )

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
