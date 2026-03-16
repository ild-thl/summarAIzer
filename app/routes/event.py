"""API routes for Event management."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    EventResponse,
    EventUpdate,
    SessionCreate,
    SessionResponse,
)
from app.security.auth import (
    get_current_user,
    require_event_owner,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/events", tags=["events"])


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

    # Create session with owner_id set
    session_in.event_id = event_id
    db_session = session_crud.create(db, session_in, owner_id=current_user.id)
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
    Create or update a session (upsert by uri within event).

    If session with (event_id, uri) exists → returns existing with metadata
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

    # Check if session exists
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
        updated = session_crud.update(db, existing.id, session_in)
        response.status_code = HTTP_201_CREATED
        return updated
    else:
        # Create new session - returns 201 (resource created)
        session_in.event_id = event_id
        created = session_crud.create(db, session_in, owner_id=current_user.id)
        response.status_code = HTTP_201_CREATED
        return created


# ============================================================================
# Embedding & Similarity Search Endpoints
# ============================================================================


@router.post("/{event_id}/embedding/refresh", response_model=dict, status_code=202)
async def refresh_event_embedding(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Manually refresh/regenerate embedding for an event.

    Requires event ownership. Queues async task - returns immediately.

    Args:
        event_id: Event ID
        current_user: Current authenticated user

    Returns:
        Task status with task_id for polling
    """
    from app.async_jobs.tasks import generate_event_embedding

    # Verify event exists and user owns it
    event = event_crud.read(db, event_id)
    if not event:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")

    if event.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden - event ownership required")

    logger.info(
        "event_embedding_refresh_requested",
        event_id=event_id,
        user_id=current_user.id,
    )

    # Queue embedding generation task
    task = generate_event_embedding.apply_async(
        args=[event_id],
        queue="embeds",
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "event_id": event_id,
        "message": "Embedding refresh queued",
    }


@router.get("/search/similar", response_model=list[EventResponse])
async def search_similar_events(
    query: str = Query(..., min_length=1, max_length=8000, description="Query text to search"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    db: Session = Depends(get_db),
):
    """
    Search for events similar to query text using semantic search.

    Returns published events ordered by semantic similarity.

    Args:
        query: Text to search (will be embedded)
        limit: Maximum number of results

    Returns:
        List of similar published events
    """
    from app.services.embedding_exceptions import (
        EmbeddingError,
        InvalidEmbeddingTextError,
    )
    from app.services.embedding_factory import get_search_service

    try:
        # Get search service via dependency injection
        search_service = get_search_service()

        # Delegate to search service
        events = await search_service.search_events(
            query=query,
            db=db,
            limit=limit,
        )

        logger.info(
            "event_search_completed",
            query_length=len(query),
            results_count=len(events),
            limit=limit,
        )

        # Convert ORM objects to Pydantic models
        return [EventResponse.model_validate(e) for e in events]

    except InvalidEmbeddingTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmbeddingError as e:
        logger.error("event_search_embedding_error", error=str(e))
        raise HTTPException(status_code=503, detail="Search service unavailable") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "event_search_failed",
            resource_type="event",
            error=str(e),
            error_type=type(e).__name__,
            query_length=len(query),
        )
        raise HTTPException(
            status_code=500,
            detail="Similarity search failed",
        ) from e
