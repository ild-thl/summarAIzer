"""API routes for Event management."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
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
from app.database.models import Event, User
from app.database.models import Session as SessionModel
from app.schemas.session import (
    EventCreate,
    EventPageResponse,
    EventResponse,
    EventUpdate,
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from app.security.auth import (
    get_current_user,
    is_admin,
    require_event_owner,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/events", tags=["events"])


def _resolve_event_sort(sort_by: str, sort_dir: str):
    """Resolve supported event sort options with stable secondary ordering."""
    sort_fields = {
        "id": Event.id,
        "title": Event.title,
        "start_date": Event.start_date,
        "created_at": Event.created_at,
        "updated_at": Event.updated_at,
    }
    column = sort_fields.get(sort_by)
    if column is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid sort_by. Allowed: id,title,start_date,created_at,updated_at",
        )

    if sort_dir == "asc":
        return [column.asc(), Event.id.asc()]

    return [column.desc(), Event.id.desc()]


@router.post("", response_model=EventResponse, status_code=HTTP_201_CREATED)
async def create_event(
    event_in: EventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new event (requires authentication).

    - **title**: Event title (required)
    - **uri**: URL-safe identifier (required, must be unique)
    - **start_date**: Event start datetime (required)
    - **end_date**: Event end datetime (required, must be after start_date)
    - **description**: Event description (optional)
    - **location**: Event location (optional)
    - **status**: Event status - draft or published (default: draft)
    """
    # Check if URI already exists
    existing = event_crud.read_by_uri(db, event_in.uri)
    if existing:
        logger.warning("event_uri_conflict", uri=event_in.uri)
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"Event with URI '{event_in.uri}' already exists",
        )

    db_event = event_crud.create(db, event_in, owner_id=current_user.id)
    return db_event


@router.get("/me", response_model=EventPageResponse)
async def list_my_events(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=200, description="Maximum records to return"),
    status: str = Query(None, description="Optional status filter (draft, published, archived)"),
    sort_by: str = Query(
        "updated_at",
        description="Sort field: id, title, start_date, created_at, updated_at",
    ),
    sort_dir: str = Query("desc", description="Sort direction: asc or desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List events manageable by current user with pagination metadata."""
    if sort_dir not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="Invalid sort_dir. Allowed: asc,desc")

    query = db.query(Event)

    if not is_admin(current_user):
        query = query.filter(Event.owner_id == current_user.id)

    if status:
        query = query.filter(Event.status == status)

    total = query.with_entities(func.count(Event.id)).scalar() or 0
    sort_columns = _resolve_event_sort(sort_by, sort_dir)
    items = query.order_by(*sort_columns).offset(skip).limit(limit).all()

    return EventPageResponse(
        items=items,
        meta={
            "total": total,
            "skip": skip,
            "limit": limit,
            "has_more": (skip + len(items)) < total,
        },
    )


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: int, db: Session = Depends(get_db)):
    """Get an event by ID."""
    db_event = event_crud.read(db, event_id)
    if not db_event:
        logger.warning("event_not_found", event_id=event_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")
    return db_event


@router.get("/by-uri/{uri}", response_model=EventResponse)
async def get_event_by_uri(uri: str, db: Session = Depends(get_db)):
    """Get an event by URI."""
    db_event = event_crud.read_by_uri(db, uri)
    if not db_event:
        logger.warning("event_not_found_by_uri", uri=uri)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")
    return db_event


@router.get("", response_model=list[EventResponse])
async def list_events(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    status: str = Query(None, description="Filter by status (draft, published, archived)"),
    db: Session = Depends(get_db),
):
    """
    List all events with optional filtering and pagination.

    - **skip**: Number of records to skip (default: 0)
    - **limit**: Maximum records to return (default: 100, max: 1000)
    - **status**: Filter by status (optional)
    """
    if status:
        events = event_crud.list_by_status(db, status, skip=skip, limit=limit)
    else:
        events = event_crud.list_all(db, skip=skip, limit=limit)
    return events


@router.patch("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    event_in: EventUpdate,
    event: Event = Depends(require_event_owner),
    db: Session = Depends(get_db),
):
    """
    Update an event partially (owner only).

    Only provide fields that need to be updated.
    """
    # Check URI conflict if URI is being updated
    if event_in.uri and event_in.uri != event.uri:
        existing = event_crud.read_by_uri(db, event_in.uri)
        if existing:
            logger.warning("event_uri_conflict_on_update", event_id=event_id, uri=event_in.uri)
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=f"Event with URI '{event_in.uri}' already exists",
            )

    updated_event = event_crud.update(db, event_id, event_in)
    return updated_event


@router.delete("/{event_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: int,
    _: Event = Depends(require_event_owner),
    db: Session = Depends(get_db),
):
    """Delete an event and all associated sessions (owner only)."""
    event_crud.delete(db, event_id)
    return None


# Nested session management endpoints under events
@router.post("/{event_id}/sessions", response_model=SessionResponse, status_code=HTTP_201_CREATED)
async def create_session_in_event(
    event_id: int,
    session_in: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new session within an event (requires authentication).
    """
    # Verify event exists and user owns it
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")
    if event.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Check if session with same URI already exists in this event
    existing = (
        db.query(SessionModel)
        .filter(
            SessionModel.event_id == event_id,
            SessionModel.uri == session_in.uri,
        )
        .first()
    )
    if existing:
        logger.warning("session_uri_conflict_in_event", event_id=event_id, uri=session_in.uri)
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"Session with URI '{session_in.uri}' already exists in this event",
        )

    # Create session with initial owner membership
    session_in.event_id = event_id
    db_session = session_crud.create(db, session_in, initial_owner_user_id=current_user.id)
    return db_session


@router.post("/{event_id}/sessions/sync", response_model=SessionResponse)
async def sync_session(
    event_id: int,
    session_in: SessionCreate,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create or update a session.

    Matching order:
    1. First match by external IDs (if provided)
    2. Fallback to (event_id, uri)

    If a match is found → update existing session
    Otherwise → creates new session

    Only event owner can sync sessions to their event.
    """
    # Verify event exists and user owns it
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")
    if event.owner_id != current_user.id:
        logger.warning("sync_unauthorized_event_access", user_id=current_user.id, event_id=event_id)
        raise HTTPException(status_code=403, detail="Permission denied")

    # Check if session exists by external IDs first
    existing: SessionModel | None = None
    if session_in.external_ids:
        for external in session_in.external_ids:
            existing = session_crud.read_by_external_id(
                db,
                label=external.label,
                external_id=external.id,
                event_id=event_id,
            )
            if existing:
                break

    # Fallback to URI-based upsert for backwards compatibility
    if not existing:
        existing = (
            db.query(SessionModel)
            .filter(
                SessionModel.event_id == event_id,
                SessionModel.uri == session_in.uri,
            )
            .first()
        )

    if existing:
        # Update existing session - returns 201 (resource processed)
        updated = session_crud.update(
            db,
            existing.id,
            SessionUpdate.model_validate(session_in.model_dump(mode="json", exclude_none=True)),
        )
        response.status_code = HTTP_201_CREATED
        return updated
    else:
        # Create new session - returns 201 (resource created)
        session_in.event_id = event_id
        created = session_crud.create(db, session_in, initial_owner_user_id=current_user.id)
        response.status_code = HTTP_201_CREATED
        return created
