"""API routes for Embedding and Semantic Search (optional feature)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.status import HTTP_202_ACCEPTED, HTTP_404_NOT_FOUND

from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import User
from app.schemas.session import (
    RecommendRequest,
    SearchIntentRefinementRequest,
    SearchIntentRefinementResponse,
    SessionResponse,
    SessionWithScore,
)
from app.security.auth import get_current_user
from app.utils.helpers import DateTimeUtils

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["embeddings"])


def _extract_query_values(query: str | list[str] | None) -> list[str]:
    """Normalize query input to a list of non-empty query strings."""
    if isinstance(query, list):
        return [item for item in query if item]
    if isinstance(query, str) and query:
        return [query]
    return []


def _is_refinement_worthy(query_values: list[str]) -> bool:
    """Return True when at least one query is substantial enough for LLM refinement."""
    for query in query_values:
        normalized = query.strip()
        if not normalized:
            continue
        words = [word for word in normalized.split() if word]
        if len(words) >= 2 or len(normalized) >= 20:
            return True
    return False


async def _apply_optional_query_refinement(
    recommend_req: "RecommendRequest",
    db: Session,
) -> tuple[
    list[str] | None, list[str] | None, list[str] | None, list[str] | None, list[str] | None, bool
]:
    """Apply query refinement when requested and return effective recommendation inputs."""
    from app.services.embedding.factory import get_query_refinement_service

    query_values = _extract_query_values(recommend_req.query)
    effective_query: list[str] | None = query_values or None

    effective_session_format = recommend_req.session_format
    effective_tags = recommend_req.tags
    effective_location_cities = recommend_req.location_cities
    effective_location_names = recommend_req.location_names

    if not (recommend_req.refine_query and query_values):
        return (
            effective_query,
            effective_session_format,
            effective_tags,
            effective_location_cities,
            effective_location_names,
            False,
        )

    if not _is_refinement_worthy(query_values):
        logger.info(
            "recommendation_query_refinement_skipped",
            reason="query_too_short_or_single_word",
            query_count=len(query_values),
        )
        return (
            effective_query,
            effective_session_format,
            effective_tags,
            effective_location_cities,
            effective_location_names,
            False,
        )

    if recommend_req.event_id is None:
        raise HTTPException(
            status_code=400,
            detail="event_id is required when refine_query=true and query is provided",
        )
    if not isinstance(recommend_req.query, list):
        raise HTTPException(
            status_code=400,
            detail="query must be a list when refine_query=true",
        )

    refinement_service = get_query_refinement_service()
    refined = await refinement_service.refine_search_intent(
        db,
        SearchIntentRefinementRequest(
            queries=query_values,
            event_id=recommend_req.event_id,
            session_format=effective_session_format,
            tags=effective_tags,
            location_cities=effective_location_cities,
            location_names=effective_location_names,
        ),
    )
    effective_query = refined.refined_queries or None

    if not effective_session_format:
        effective_session_format = refined.session_format
    if not effective_tags:
        effective_tags = refined.tags
    if not effective_location_cities:
        effective_location_cities = refined.location_cities

    return (
        effective_query,
        effective_session_format,
        effective_tags,
        effective_location_cities,
        effective_location_names,
        True,
    )


@router.post("/query/refine", response_model=SearchIntentRefinementResponse)
async def refine_search_intent(
    request_body: SearchIntentRefinementRequest,
    db: Session = Depends(get_db),
):
    """Refine a free-text session query and infer missing hard filters when strongly implied."""
    from app.services.embedding.exceptions import QueryRefinementError
    from app.services.embedding.factory import get_query_refinement_service

    try:
        refinement_service = get_query_refinement_service()
        return await refinement_service.refine_search_intent(db, request_body)
    except QueryRefinementError as e:
        logger.error("search_intent_refinement_error", error=str(e))
        raise HTTPException(status_code=503, detail="Query refinement service unavailable") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "search_intent_refinement_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="Query refinement failed") from e


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
    session_format: str | None = Query(
        None,
        description="Filter by session format - comma-separated (Input, Lighting Talk, Diskussion, workshop, Training) - OR logic",
    ),
    tags: str | None = Query(None, description="Filter by tags (comma-separated, OR logic)"),
    location_cities: str | None = Query(
        None, description="Filter by city (comma-separated, OR logic)"
    ),
    location_names: str | None = Query(
        None,
        description="Filter by location name such as stage or room (comma-separated, OR logic)",
    ),
    language: str | None = Query(
        None,
        description="Filter by language - comma-separated (ISO 639-1 code, e.g., en,de) - OR logic",
    ),
    duration_min: int | None = Query(None, ge=0, description="Minimum duration in minutes"),
    duration_max: int | None = Query(None, ge=0, description="Maximum duration in minutes"),
    time_windows: str | None = Query(
        None,
        description='JSON array of time windows, e.g. [{"start":"2024-06-01T10:00:00","end":"2024-06-01T11:30:00"}]',
    ),
    db: Session = Depends(get_db),
):
    """
    Search for sessions similar to query text using semantic search with optional filtering.

    Returns published sessions ordered by semantic similarity.

    **Parameters:**
    - **query**: Text to search (will be embedded and matched semantically)
    - **limit**: Maximum number of results (1-100)
    - **event_id**: Optional filter by event ID (applied at DB level)
    - **session_format**: Optional filter by format - comma-separated (Input, Lighting Talk, Diskussion, workshop, Training) - OR logic
    - **tags**: Optional filter by tags (comma-separated, OR logic - matches any tag)
    - **location**: Optional filter by location (comma-separated, OR logic - matches any location)
    - **language**: Optional filter by language code - comma-separated (e.g., en, de, fr) - OR logic
    - **duration_min**: Optional minimum duration in minutes
    - **duration_max**: Optional maximum duration in minutes
    - **time_windows**: Optional JSON array of windows; sessions must fit within at least one window

    **Examples:**
    - `/api/v2/sessions/search/similar?query=machine+learning`
    - `/api/v2/sessions/search/similar?query=AI&event_id=5&language=en`
    - `/api/v2/sessions/search/similar?query=AI&event_id=5&language=en,de`
    - `/api/v2/sessions/search/similar?query=ethics&tags=ai,security&session_format=workshop,input`
    - `/api/v2/sessions/search/similar?query=keynote&location=Landing:Stage+Berlin,AI:Stage+TU+Graz`
    - `/api/v2/sessions/search/similar?query=workshop&time_windows=[{"start":"2024-06-01T10:00:00","end":"2024-06-01T11:30:00"}]` (timeframe)
    - `/api/v2/sessions/search/similar?query=learning&duration_min=30&duration_max=90`
    - `/api/v2/sessions/search/similar?query=workshop&time_windows=[{"start":"2024-01-01T00:00:00","end":"2024-12-31T23:59:59"}]`
    """
    from app.services.embedding.exceptions import (
        EmbeddingError,
        InvalidEmbeddingTextError,
    )
    from app.services.embedding.factory import get_search_service

    try:
        from app.database.models import SessionFormat

        # Validate and parse session_format (comma-separated enum values)
        session_format_list = None
        if session_format:
            from app.routes.session import _validate_and_parse_enum_list

            session_format_list = _validate_and_parse_enum_list(
                session_format, SessionFormat, "session_format"
            )

        # Parse language (comma-separated, normalize to lowercase for consistency)
        language_list = None
        if language:
            language_list = [lang.strip().lower() for lang in language.split(",") if lang.strip()]

        # Parse tags and locations (comma-separated)
        tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        location_cities_list = (
            [c.strip() for c in location_cities.split(",") if c.strip()]
            if location_cities
            else None
        )
        location_names_list = (
            [n.strip() for n in location_names.split(",") if n.strip()] if location_names else None
        )

        # Parse unified time windows
        parsed_time_windows = DateTimeUtils.parse_time_windows_json(time_windows)

        # Get search service and invoke
        search_service = get_search_service()

        # Delegate to search service with optional filters
        sessions = await search_service.search_sessions(
            query=query,
            db=db,
            limit=limit,
            event_id=event_id,
            session_format=session_format_list,
            tags=tags_list,
            location_cities=location_cities_list,
            location_names=location_names_list,
            language=language_list,
            duration_min=duration_min,
            duration_max=duration_max,
            time_windows=parsed_time_windows,
        )

        logger.info(
            "session_search_completed",
            query_length=len(query),
            results_count=len(sessions),
            limit=limit,
            event_id=event_id,
            filters_applied=bool(
                session_format_list
                or tags_list
                or location_cities_list
                or location_names_list
                or language_list
                or duration_min
                or duration_max
                or parsed_time_windows
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
    - **Filters**: Format/language/tags/location/duration filters apply as hard constraints
    - **goal_mode**: `similarity` (default) or `plan` (build non-overlapping session schedule)
    - **time_windows**: Optional list of time windows used for filtering and in `plan` mode
    - **min_break_minutes**: Minimum break between selected sessions in `plan` mode
    - **max_gap_minutes**: Optional max allowed gap between selected sessions in `plan` mode
    - **plan_candidate_multiplier**: Candidate pool expansion factor before plan optimization

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
                "goal_mode": "plan",
                "time_windows": [{"start": "2024-06-01T10:00:00", "end": "2024-06-01T11:30:00"}],
        "limit": 5
      }
      ```
        - Plan mode: Build a non-overlapping schedule for a multi-day event
            ```json
            {
                "query": "machine learning",
                "goal_mode": "plan",
                "time_windows": [
                    {"start": "2024-06-01T09:00:00", "end": "2024-06-01T18:00:00"},
                    {"start": "2024-06-02T09:00:00", "end": "2024-06-02T18:00:00"},
                    {"start": "2024-06-03T09:00:00", "end": "2024-06-03T17:00:00"}
                ],
                "min_break_minutes": 15,
                "max_gap_minutes": 90,
                "plan_candidate_multiplier": 4,
                "limit": 5
            }
            ```
        - Plan mode without query: Build a schedule from liked sessions and filters
            ```json
            {
                "accepted_ids": [10, 14],
                "goal_mode": "plan",
                "time_windows": [{"start": "2024-06-01T09:00:00", "end": "2024-06-01T18:00:00"}],
                "session_format": "workshop",
                "language": "en",
                "min_break_minutes": 10,
                "limit": 4
            }
            ```
    """
    from app.services.embedding.exceptions import (
        EmbeddingError,
        EmbeddingSearchError,
        InvalidEmbeddingTextError,
        QueryRefinementError,
    )
    from app.services.embedding.factory import get_recommendation_service

    try:
        # Validate request
        recommend_req = request_body

        (
            effective_query,
            effective_session_format,
            effective_tags,
            effective_location_cities,
            effective_location_names,
            query_refined,
        ) = await _apply_optional_query_refinement(recommend_req=recommend_req, db=db)

        # Get recommendation service
        recommendation_service = get_recommendation_service()

        # Call recommender with Phase 2 re-ranking + Phase 3 soft filter parameters
        sessions = await recommendation_service.recommend_sessions(
            db=db,
            accepted_ids=recommend_req.accepted_ids,
            rejected_ids=recommend_req.rejected_ids,
            query=effective_query,
            limit=recommend_req.limit,
            event_id=recommend_req.event_id,
            session_format=effective_session_format,
            tags=effective_tags,
            location_cities=effective_location_cities,
            location_names=effective_location_names,
            language=recommend_req.language,
            duration_min=recommend_req.duration_min,
            duration_max=recommend_req.duration_max,
            liked_embedding_weight=recommend_req.liked_embedding_weight,
            disliked_embedding_weight=recommend_req.disliked_embedding_weight,
            filter_mode=recommend_req.filter_mode,
            filter_margin_weight=recommend_req.filter_margin_weight,
            diversity_weight=recommend_req.diversity_weight,
            goal_mode=recommend_req.goal_mode,
            time_windows=recommend_req.time_windows,
            min_break_minutes=recommend_req.min_break_minutes,
            max_gap_minutes=recommend_req.max_gap_minutes,
            plan_candidate_multiplier=recommend_req.plan_candidate_multiplier,
        )

        logger.info(
            "recommendations_completed",
            query_provided=bool(effective_query),
            query_refined=query_refined,
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
                filter_compliance_score=scores["filter_compliance_score"],
                diversity_score=scores.get("diversity_score"),
            )
            for session, scores in sessions
        ]

    except InvalidEmbeddingTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except QueryRefinementError as e:
        logger.error("recommendation_query_refinement_error", error=str(e))
        raise HTTPException(status_code=503, detail="Query refinement service unavailable") from e
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
