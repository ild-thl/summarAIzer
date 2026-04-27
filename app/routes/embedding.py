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
from app.utils.matomo import track_recommend_usage

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


@router.post(
    "/embeddings/reconcile",
    response_model=dict,
    status_code=HTTP_202_ACCEPTED,
)
async def reconcile_embeddings(
    event_id: int | None = Query(None, description="Optional event scope for reconciliation"),
    current_user: User = Depends(get_current_user),
):
    """Return immediate reconciliation stats and queue async refresh execution."""
    from app.async_jobs.tasks import reconcile_session_embeddings

    preview = reconcile_session_embeddings.apply(
        kwargs={"event_id": event_id, "enqueue_refreshes": False}
    ).get()

    task = reconcile_session_embeddings.apply_async(
        kwargs={"event_id": event_id},
        queue="embeds",
    )

    will_reembed = min(preview.get("to_reembed", 0), preview.get("max_enqueues", 0))

    logger.info(
        "session_embedding_reconcile_requested",
        user_id=current_user.id,
        event_id=event_id,
        task_id=task.id,
        scanned=preview.get("scanned", 0),
        synced=preview.get("synced", 0),
        missing=preview.get("missing", 0),
        stale=preview.get("stale", 0),
        to_reembed=preview.get("to_reembed", 0),
        will_reembed=will_reembed,
        orphaned=preview.get("orphaned", 0),
        deleted_orphans=preview.get("deleted_orphans", 0),
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "event_id": event_id,
        "scanned": preview.get("scanned", 0),
        "synced": preview.get("synced", 0),
        "missing": preview.get("missing", 0),
        "stale": preview.get("stale", 0),
        "to_reembed": preview.get("to_reembed", 0),
        "will_reembed": will_reembed,
        "orphaned": preview.get("orphaned", 0),
        "deleted_orphans": preview.get("deleted_orphans", 0),
        "max_enqueues": preview.get("max_enqueues", 0),
        "message": "Embedding reconciliation queued with preview statistics",
    }


@router.get("/search/similar", response_model=list[SessionWithScore])
async def search_similar_sessions(
    query: str = Query(..., min_length=1, max_length=8000, description="Query text to search"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    event_id: int | None = Query(None, description="Filter by event ID"),
    session_format: str | None = Query(
        None,
        description="Filter by session format - comma-separated (input, lighting talk, diskussion, workshop, training, lab, other) - OR logic",
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
    - **session_format**: Optional filter by format - comma-separated (Input, Lighting Talk, Diskussion, workshop, Training, lab, other) - OR logic
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


@router.post(
    "/recommend",
    response_model=list[SessionWithScore],
    dependencies=[Depends(track_recommend_usage)],
)
async def recommend_sessions(
    request_body: "RecommendRequest",
    db: Session = Depends(get_db),
):
    """
    Get personalized session recommendations.

    This endpoint supports two modes:
    - `similarity`: rank sessions by semantic and preference similarity
    - `plan`: generate a non-overlapping schedule from candidate sessions

    Filter behavior:
    - By default (`soft_filters=null`), all provided filters are applied strictly during retrieval.
    - Set `soft_filters` to a list of attribute names (e.g. `["session_format", "tags", "location", "language", "duration", "time_windows"]`) to
        apply those filters as soft scoring rather than hard retrieval constraints.

    Optional query refinement:
    - Set `refine_query=true` to infer/improve search intent from a list of query strings.
    - When enabled, `event_id` is required and `query` must be a list.

    Examples:
    - Similarity mode with event scope:
        ```json
        {
            "event_id": 3,
            "query": "machine learning",
            "accepted_ids": [],
            "rejected_ids": [1, 2, 3],
            "session_format": ["workshop"],
            "language": ["en"],
            "limit": 10
        }
        ```
    - Soft filtering (rank-oriented):
        ```json
        {
            "event_id": 3,
            "query": ["ai ethics"],
            "soft_filters": ["tags", "location", "time_windows"],
            "tags": ["ethics", "policy"],
            "location_cities": ["Berlin"],
            "time_windows": [
                {"start": "2024-06-01T09:00:00", "end": "2024-06-01T12:00:00"}
            ],
            "limit": 10
        }
        ```
    - Plan mode with windows:
        ```json
        {
            "event_id": 3,
            "goal_mode": "plan",
            "query": ["machine learning"],
            "time_windows": [
                {"start": "2024-06-01T09:00:00", "end": "2024-06-01T18:00:00"},
                {"start": "2024-06-02T09:00:00", "end": "2024-06-02T18:00:00"}
            ],
            "min_break_minutes": 15,
            "max_gap_minutes": 90,
            "plan_candidate_multiplier": 2,
            "limit": 5
        }
        ```
    """
    from app.crud.event import event_crud
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

        if recommend_req.event_id is not None:
            event = event_crud.read(db, recommend_req.event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Event not found")

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
            exclude_parallel_accepted_sessions=recommend_req.exclude_parallel_accepted_sessions,
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
            preference_dominance_margin=recommend_req.preference_dominance_margin,
            soft_filters=recommend_req.soft_filters,
            filter_margin_weight=recommend_req.filter_margin_weight,
            min_overall_score=recommend_req.min_overall_score,
            diversity_weight=recommend_req.diversity_weight,
            goal_mode=recommend_req.goal_mode,
            time_windows=recommend_req.time_windows,
            min_break_minutes=recommend_req.min_break_minutes,
            max_gap_minutes=recommend_req.max_gap_minutes,
            plan_candidate_multiplier=recommend_req.plan_candidate_multiplier,
        )

        logger.debug(
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
