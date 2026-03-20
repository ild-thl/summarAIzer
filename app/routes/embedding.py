"""API routes for Embedding and Semantic Search (optional feature)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.status import HTTP_202_ACCEPTED, HTTP_404_NOT_FOUND

from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import User
from app.schemas.session import RecommendRequest, SessionResponse, SessionWithScore
from app.security.auth import get_current_user
from app.utils.helpers import DateTimeUtils

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


@router.get("/search/similar", response_model=list[SessionWithScore])
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

    try:
        # Parse tags and location (comma-separated)
        tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        location_list = (
            [loc.strip() for loc in location.split(",") if loc.strip()] if location else None
        )
        normalized_language = language.lower() if language else None

        # Parse datetime filters using helper
        start_after_dt, start_before_dt, end_after_dt, end_before_dt = (
            DateTimeUtils.parse_datetime_filters(start_after, start_before, end_after, end_before)
        )

        # Get search service and invoke
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

        # Convert to response models with scores
        return [
            SessionWithScore(
                session=SessionResponse.model_validate(session),
                overall_score=scores["overall_score"],
                semantic_similarity=scores["semantic_similarity"],
                liked_cluster_similarity=scores["liked_cluster_similarity"],
                disliked_similarity=scores["disliked_similarity"],
                filter_match_ratio=scores["filter_match_ratio"],
                explanation=scores["explanation"],
            )
            for session, scores in sessions
        ]

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


@router.post("/recommend", response_model=list[SessionWithScore])
async def recommend_sessions(
    request_body: "RecommendRequest",
    db: Session = Depends(get_db),
):
    """
    Get personalized session recommendations based on user preferences.

    Recommends sessions similar to those the user has liked, excluding sessions they've
    already seen. Supports optional text query (if provided, overrides centroid from liked sessions).

    **Request Body:**
    - **query**: Optional text query. If not provided, recommendations use centroid of liked sessions.
    - **accepted_ids**: List of session IDs the user has liked or want to get more like
    - **rejected_ids**: List of session IDs the user has disliked (excluded from results)
    - **limit**: Max recommendations (1-100)
    - **Filters**: All standard session filters (format, language, duration, etc.) apply as hard constraints

    **Examples:**
    - Basic: User did not like sessions [1, 2] and liked session [5], give me more like [5]
      ```json
      {
        "accepted_ids": [5],
        "rejected_ids": [1, 2],
        "limit": 10
      }
      ```
    - With query: Find workshops similar to "machine learning" but exclude sessions 1, 2, 3
      ```json
      {
        "query": "machine learning",
        "accepted_ids": [],
        "rejected_ids": [1, 2, 3],
        "session_format": "workshop",
        "language": "en",
        "limit": 10
      }
      ```
    - With timeframe: Recommendations for 10:00-11:30 timeframe that are similar to loved session 42
      ```json
      {
        "accepted_ids": [42],
        "rejected_ids": [],
        "start_after": "2024-06-01T10:00:00",
        "end_before": "2024-06-01T11:30:00",
        "limit": 5
      }
      ```
    """
    from app.services.embedding_exceptions import (
        EmbeddingError,
        EmbeddingSearchError,
        InvalidEmbeddingTextError,
    )
    from app.services.embedding_factory import get_search_service

    try:
        # Validate request
        recommend_req = request_body

        # Parse datetime values from request body (may be datetime objects or strings)
        start_after_dt = DateTimeUtils.parse_datetime_or_none(recommend_req.start_after)
        start_before_dt = DateTimeUtils.parse_datetime_or_none(recommend_req.start_before)
        end_after_dt = DateTimeUtils.parse_datetime_or_none(recommend_req.end_after)
        end_before_dt = DateTimeUtils.parse_datetime_or_none(recommend_req.end_before)

        # Get search service
        search_service = get_search_service()

        # Call recommender with Phase 2 re-ranking parameters
        sessions = await search_service.recommend_sessions(
            db=db,
            accepted_ids=recommend_req.accepted_ids,
            rejected_ids=recommend_req.rejected_ids,
            query=recommend_req.query,
            limit=recommend_req.limit,
            event_id=recommend_req.event_id,
            session_format=recommend_req.session_format,
            tags=recommend_req.tags,
            location=recommend_req.location,
            language=recommend_req.language,
            duration_min=recommend_req.duration_min,
            duration_max=recommend_req.duration_max,
            start_after=start_after_dt,
            start_before=start_before_dt,
            end_after=end_after_dt,
            end_before=end_before_dt,
            liked_embedding_weight=recommend_req.liked_embedding_weight,
            disliked_embedding_weight=recommend_req.disliked_embedding_weight,
        )

        logger.info(
            "recommendations_completed",
            query_provided=bool(recommend_req.query),
            accepted_ids_count=len(recommend_req.accepted_ids or []),
            rejected_ids_count=len(recommend_req.rejected_ids or []),
            recommendations_count=len(sessions),
            limit=recommend_req.limit,
        )

        # Convert to response models with scores
        return [
            SessionWithScore(
                session=SessionResponse.model_validate(session),
                overall_score=scores["overall_score"],
                semantic_similarity=scores["semantic_similarity"],
                liked_cluster_similarity=scores["liked_cluster_similarity"],
                disliked_similarity=scores["disliked_similarity"],
                filter_match_ratio=scores["filter_match_ratio"],
                explanation=scores["explanation"],
            )
            for session, scores in sessions
        ]

    except InvalidEmbeddingTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmbeddingSearchError as e:
        logger.error("recommendation_search_error", error=str(e))
        raise HTTPException(status_code=503, detail="Recommendation service unavailable") from e
    except EmbeddingError as e:
        logger.error("recommendation_embedding_error", error=str(e))
        raise HTTPException(status_code=503, detail="Embedding service unavailable") from e
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "recommendation_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Recommendation failed",
        ) from e
