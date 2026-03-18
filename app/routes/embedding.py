"""API routes for Embedding and Semantic Search (optional feature)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.status import HTTP_202_ACCEPTED, HTTP_404_NOT_FOUND

from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import User
from app.schemas.session import SessionResponse
from app.security.auth import get_current_user

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["embeddings"])


@router.post(
    "/{session_id}/embedding/refresh",
    response_model=dict,
    status_code=HTTP_202_ACCEPTED,
)
async def refresh_session_embedding(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Manually refresh/regenerate embedding for a session.

    Requires session ownership. Queues async task - returns immediately.

    Args:
        session_id: Session ID
        current_user: Current authenticated user

    Returns:
        Task status with task_id for polling
    """
    from app.async_jobs.tasks import generate_session_embedding

    # Verify session exists and user owns it
    session = session_crud.read(db, session_id)
    if not session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if session.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden - session ownership required")

    logger.info(
        "session_embedding_refresh_requested",
        session_id=session_id,
        user_id=current_user.id,
    )

    # Queue embedding generation task
    task = generate_session_embedding.apply_async(
        args=[session_id],
        queue="embeds",
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "session_id": session_id,
        "message": "Embedding refresh queued",
    }


@router.get("/search/similar", response_model=list[SessionResponse])
async def search_similar_sessions(
    query: str = Query(..., min_length=1, max_length=8000, description="Query text to search"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    event_id: int | None = Query(None, description="Filter by event ID"),
    session_format: str | None = Query(None, description="Filter by session format"),
    tags: str | None = Query(None, description="Filter by tags (comma-separated, OR logic)"),
    location: str | None = Query(
        None, description="Filter by location (comma-separated, OR logic)"
    ),
    language: str | None = Query(None, description="Filter by language (ISO 639-1 code)"),
    duration_min: int | None = Query(None, ge=0, description="Minimum duration in minutes"),
    duration_max: int | None = Query(None, ge=0, description="Maximum duration in minutes"),
    start_after: str | None = Query(None, description="Sessions starting after (ISO 8601)"),
    start_before: str | None = Query(None, description="Sessions starting before (ISO 8601)"),
    end_after: str | None = Query(None, description="Sessions ending after (ISO 8601)"),
    end_before: str | None = Query(None, description="Sessions ending before (ISO 8601)"),
    db: Session = Depends(get_db),
):
    """
    Search for sessions similar to query text using semantic search with optional filtering.

    Returns published sessions ordered by semantic similarity.

    **Parameters:**
    - **query**: Text to search (will be embedded and matched semantically)
    - **limit**: Maximum number of results (1-100)
    - **event_id**: Optional filter by event ID (applied at DB level)
    - **session_format**: Optional filter by format (applied via Chroma metadata)
    - **tags**: Optional filter by tags (comma-separated, OR logic - matches any tag)
    - **location**: Optional filter by location (comma-separated, OR logic - matches any location)
    - **language**: Optional filter by language code (applied via Chroma metadata)
    - **duration_min**: Optional minimum duration in minutes
    - **duration_max**: Optional maximum duration in minutes
    - **start_after**: Optional sessions starting after date (ISO 8601, e.g., 2024-01-01T00:00:00)
    - **start_before**: Optional sessions starting before date (ISO 8601)
    - **end_after**: Optional sessions ending after date (ISO 8601)
    - **end_before**: Optional sessions ending before date (ISO 8601)

    **Examples:**
    - `/api/v2/sessions/search/similar?query=machine+learning`
    - `/api/v2/sessions/search/similar?query=AI&event_id=5&language=en`
    - `/api/v2/sessions/search/similar?query=ethics&tags=ai,security&session_format=talk`
    - `/api/v2/sessions/search/similar?query=keynote&location=Landing:Stage+Berlin,AI:Stage+TU+Graz`
    - `/api/v2/sessions/search/similar?query=workshop&start_after=2024-06-01T10:00:00&end_before=2024-06-01T11:30:00` (timeframe)
    - `/api/v2/sessions/search/similar?query=learning&duration_min=30&duration_max=90`
    - `/api/v2/sessions/search/similar?query=workshop&start_after=2024-01-01&start_before=2024-12-31`
    """
    from app.services.embedding_exceptions import (
        EmbeddingError,
        InvalidEmbeddingTextError,
    )
    from app.services.embedding_factory import get_search_service
    from datetime import datetime

    try:
        # Parse tags (comma-separated)
        tags_list = None
        if tags:
            tags_list = [t.strip() for t in tags.split(",") if t.strip()]

        # Parse location (comma-separated)
        location_list = None
        if location:
            location_list = [loc.strip() for loc in location.split(",") if loc.strip()]

        # Normalize language to lowercase for consistency
        normalized_language = language.lower() if language else None

        # Parse datetime strings if provided
        start_after_dt = None
        start_before_dt = None
        end_after_dt = None
        end_before_dt = None
        if start_after:
            try:
                start_after_dt = datetime.fromisoformat(start_after.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail="Invalid start_after format (use ISO 8601)"
                ) from e
        if start_before:
            try:
                start_before_dt = datetime.fromisoformat(start_before.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail="Invalid start_before format (use ISO 8601)"
                ) from e
        if end_after:
            try:
                end_after_dt = datetime.fromisoformat(end_after.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail="Invalid end_after format (use ISO 8601)"
                ) from e
        if end_before:
            try:
                end_before_dt = datetime.fromisoformat(end_before.replace("Z", "+00:00"))
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail="Invalid end_before format (use ISO 8601)"
                ) from e

        # Get search service via dependency injection
        search_service = get_search_service()

        # Delegate to search service with optional filters
        sessions = await search_service.search_sessions(
            query=query,
            db=db,
            limit=limit,
            event_id=event_id,
            session_format=session_format,
            tags=tags_list,
            location=location_list,
            language=normalized_language,
            duration_min=duration_min,
            duration_max=duration_max,
            start_after=start_after_dt,
            start_before=start_before_dt,
            end_after=end_after_dt,
            end_before=end_before_dt,
        )

        logger.info(
            "session_search_completed",
            query_length=len(query),
            results_count=len(sessions),
            limit=limit,
            event_id=event_id,
            filters_applied=bool(
                session_format
                or tags_list
                or location_list
                or language
                or duration_min
                or duration_max
                or start_after_dt
                or start_before_dt
                or end_after_dt
                or end_before_dt
            ),
        )

        # Convert ORM objects to Pydantic models
        return [SessionResponse.model_validate(s) for s in sessions]

    except InvalidEmbeddingTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmbeddingError as e:
        logger.error("session_search_embedding_error", error=str(e))
        raise HTTPException(status_code=503, detail="Search service unavailable") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "session_search_failed",
            error=str(e),
            error_type=type(e).__name__,
            query_length=len(query),
        )
        raise HTTPException(
            status_code=500,
            detail="Similarity search failed",
        ) from e
