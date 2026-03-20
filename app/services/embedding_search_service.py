"""
Semantic search orchest ration service.

Coordinates embedding generation, Chroma queries, and database fetches
to provide a clean interface for similarity search across sessions/events.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.services.embedding_exceptions import (
    EmbeddingSearchError,
    InvalidEmbeddingTextError,
)
from app.services.embedding_service import EmbeddingService
from app.utils.helpers import DateTimeUtils

logger = structlog.get_logger()


class EmbeddingSearchService:
    """Orchestrates semantic search: embedding → Chroma query → database fetch."""

    def __init__(self, embedding_service: EmbeddingService):
        """
        Initialize search service.

        Args:
            embedding_service: Initialized EmbeddingService instance
        """
        self.embedding_service = embedding_service

    def _build_location_condition(self, location: list[str] | None) -> dict | None:
        """Build location condition for Chroma filtering."""
        if not location:
            return None
        location_conditions = [{"location": loc} for loc in location]
        if len(location_conditions) == 1:
            return location_conditions[0]
        return {"$or": location_conditions}

    def _build_tags_condition(self, tags: list[str] | None) -> dict | None:
        """Build tags condition for Chroma filtering."""
        if not tags:
            return None
        tag_conditions = [{"tags": {"$contains": tag}} for tag in tags]
        if len(tag_conditions) == 1:
            return tag_conditions[0]
        return {"$or": tag_conditions}

    def _build_time_windows_conditions(self, time_windows: list[Any] | None) -> dict | None:
        """Build OR-ed Chroma conditions for time windows."""
        if not time_windows:
            return None

        window_conditions = []
        for window in time_windows:
            start, end = self._extract_window_bounds(window)
            if start is None or end is None:
                continue
            window_conditions.append(
                {
                    "$and": [
                        {"start_datetime": {"$gte": start.timestamp()}},
                        {"end_datetime": {"$lte": end.timestamp()}},
                    ]
                }
            )

        if not window_conditions:
            return None
        if len(window_conditions) == 1:
            return window_conditions[0]
        return {"$or": window_conditions}

    def _build_simple_conditions(
        self,
        session_format: str | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
    ) -> list[dict]:
        """Build simple field conditions for Chroma filtering."""
        conditions = []
        if session_format:
            conditions.append({"session_format": session_format})
        if language:
            conditions.append({"language": language})
        if duration_min is not None:
            conditions.append({"duration": {"$gte": duration_min}})
        if duration_max is not None:
            conditions.append({"duration": {"$lte": duration_max}})
        return conditions

    def _build_chroma_conditions(
        self,
        session_format: str | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        time_windows: list[Any] | None = None,
    ) -> dict | None:
        """Build Chroma WHERE filter conditions from parameters.

        Delegates to helper methods for different condition types.
        Returns None if no conditions, dict otherwise.
        """
        # Collect all conditions
        all_conditions = []
        all_conditions.extend(
            self._build_simple_conditions(session_format, language, duration_min, duration_max)
        )

        # Add complex conditions
        location_condition = self._build_location_condition(location)
        if location_condition:
            all_conditions.append(location_condition)

        tags_condition = self._build_tags_condition(tags)
        if tags_condition:
            all_conditions.append(tags_condition)

        time_window_condition = self._build_time_windows_conditions(time_windows)
        if time_window_condition:
            all_conditions.append(time_window_condition)

        # Combine conditions
        if not all_conditions:
            return None
        if len(all_conditions) == 1:
            return all_conditions[0]
        return {"$and": all_conditions}

    def _combine_conditions(self, condition1: dict | None, condition2: dict | None) -> dict | None:
        """Combine two Chroma WHERE conditions with AND logic."""
        if not condition1 and not condition2:
            return None
        if not condition1:
            return condition2
        if not condition2:
            return condition1
        return {"$and": [condition1, condition2]}

    def _has_active_filters(
        self,
        event_id: int | None,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
    ) -> bool:
        """Check if any filters are active."""
        return bool(
            event_id
            or session_format
            or tags
            or location
            or language
            or duration_min
            or duration_max
            or time_windows
        )

    async def search_by_collection(
        self,
        query: str,
        db: Session,
        search_fn: Callable,
        crud_read: Callable,
        status_filter: Any,
        limit: int = 10,
        extra_filter: Callable[[Any], bool] | None = None,
        entity_name: str = "entity",
        chroma_where: dict | None = None,
    ) -> list:
        """
        Generic search across any collection with optional Chroma filtering.

        Args:
            query: Search query text
            db: Database session
            search_fn: Function to call on embedding_service (e.g., search_similar_sessions)
            crud_read: CRUD read function to fetch entities (e.g., session_crud.read)
            status_filter: Status enum value to filter by (e.g., SessionStatus.PUBLISHED)
            limit: Maximum results (1-100)
            extra_filter: Optional callback for additional filtering (e.g., event_id filter)
            entity_name: Name for logging
            chroma_where: Optional Chroma where filter dict for metadata filtering at search time

        Returns:
            List of similar entities

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If search fails
        """
        if not EmbeddingService.validate_embedding_text(query):
            raise InvalidEmbeddingTextError("Query text is invalid or too long")

        try:
            # Generate embedding for query
            query_embedding = await self.embedding_service.embed_query(query)

            # Search Chroma for similar IDs with optional metadata filters
            if chroma_where:
                chroma_results = await search_fn(query_embedding, limit=limit, where=chroma_where)
            else:
                chroma_results = await search_fn(query_embedding, limit=limit)

            # Extract IDs from Chroma results
            entity_ids = [entity_id for entity_id, _, _ in chroma_results]

            # Fetch full entities from database, apply filters
            entities = []
            for entity_id in entity_ids:
                entity = crud_read(db, entity_id)
                if entity and entity.status == status_filter:
                    # Apply extra filter if provided
                    if extra_filter and not extra_filter(entity):
                        continue
                    entities.append(entity)

            logger.info(
                f"{entity_name}_search_completed",
                query_length=len(query),
                chroma_results=len(chroma_results),
                database_results=len(entities),
                limit=limit,
                chroma_filters_applied=bool(chroma_where),
            )

            return entities

        except InvalidEmbeddingTextError:
            raise
        except Exception as e:
            logger.error(
                f"{entity_name}_search_failed",
                error=str(e),
                error_type=type(e).__name__,
                query_length=len(query),
                limit=limit,
            )
            raise EmbeddingSearchError(f"{entity_name} search failed: {e!s}") from e

    async def search_sessions(
        self,
        query: str,
        db: Session,
        limit: int = 10,
        event_id: int | None = None,
        session_format: str | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        time_windows: list[Any] | None = None,
    ) -> list[tuple]:
        """
        Search for similar sessions by query text with optional filtering.

        Args:
            query: Search query text
            db: Database session
            limit: Maximum results (1-100)
            event_id: Optional filter by event ID (applied at DB level)
            session_format: Optional filter by format (applied via Chroma metadata)
            tags: Optional filter by tags (applied via Chroma metadata)
            location: Optional filter by location (applied via Chroma metadata)
            language: Optional filter by language (applied via Chroma metadata)
            duration_min: Optional minimum duration in minutes (applied via Chroma metadata)
            duration_max: Optional maximum duration in minutes (applied via Chroma metadata)
            time_windows: Optional list of time windows (applied via Chroma metadata)

        Returns:
            List of tuples: (session, scores_dict) with similarity metrics

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If search fails
        """
        if not EmbeddingService.validate_embedding_text(query):
            raise InvalidEmbeddingTextError("Query text is invalid or too long")

        try:
            # Generate embedding for query
            query_embedding = await self.embedding_service.embed_query(query)

            # Build Chroma where filter using helper
            chroma_where = self._build_chroma_conditions(
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
            )

            # Search Chroma with filters
            if chroma_where:
                chroma_results = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=limit, where=chroma_where
                )
            else:
                chroma_results = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=limit
                )

            # Fetch from DB, apply filters, and compute scores
            results = []
            for session_id, chroma_similarity, _ in chroma_results:
                session = session_crud.read(db, session_id)
                if not session or session.status != SessionStatus.PUBLISHED:
                    continue

                # Apply event_id filter if provided
                if event_id and session.event_id != event_id:
                    continue

                # Compute scores for search results
                scores = {
                    "overall_score": round(chroma_similarity, 3),
                    "semantic_similarity": round(chroma_similarity, 3),
                    "liked_cluster_similarity": None,
                    "disliked_similarity": None,
                    "filter_match_ratio": 1.0,
                    "explanation": f"semantic similarity: {chroma_similarity:.2f}",
                }

                results.append((session, scores))

                if len(results) >= limit:
                    break

            logger.info(
                "session_search_completed",
                query_length=len(query),
                chroma_results=len(chroma_results),
                database_results=len(results),
                limit=limit,
            )

            return results

        except InvalidEmbeddingTextError:
            raise
        except EmbeddingSearchError:
            raise
        except Exception as e:
            logger.error(
                "session_search_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmbeddingSearchError(f"Session search failed: {e!s}") from e

    async def recommend_sessions(
        self,
        db: Session,
        accepted_ids: list[int] | None = None,
        rejected_ids: list[int] | None = None,
        query: str | None = None,
        limit: int = 10,
        event_id: int | None = None,
        session_format: str | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
        filter_mode: str = "hard",
        filter_margin_weight: float = 0.1,
        soft_filter_limit_ratio: float = 0.5,
        goal_mode: str = "similarity",
        time_windows: list[Any] | None = None,
        min_break_minutes: int = 0,
        max_gap_minutes: int | None = None,
        plan_candidate_multiplier: int = 3,
    ) -> list[tuple]:
        """
        Recommend sessions based on user preferences and optional filters.

        **Phase 2 Features:** Similarity-based re-ranking using embedding comparisons.
        Final score = base_score + a*liked_sim - b*disliked_sim

        **Phase 3 Features:** Soft filter margins expand candidate pools when hard filters
        are too restrictive, while maintaining transparency via compliance scoring.

        Supports three execution modes:
        1. With query: Semantic search using provided text
        2. With accepted_ids (no query): Centroid-based search from liked sessions
        3. No query & no accepted_ids: CRUD fallback with efficient filtering (filters + exclusions)

        All inputs are optional. Rejected IDs are excluded from results in all modes.

        Args:
            db: Database session
            accepted_ids: Session IDs the user has liked (centroid source if no query)
            rejected_ids: Session IDs the user has disliked (always excluded)
            query: Optional text query for semantic search
            limit: Maximum results (1-100)
            event_id: Optional filter by event ID
            session_format: Optional filter by format
            tags: Optional filter by tags (OR logic)
            location: Optional filter by location (OR logic)
            language: Optional filter by language
            duration_min: Optional minimum duration in minutes
            duration_max: Optional maximum duration in minutes
            liked_embedding_weight: Weight (a) to boost sessions similar to liked sessions (0-1, default 0.3)
            disliked_embedding_weight: Weight (b) to penalize sessions similar to disliked sessions (0-1, default 0.2)
            filter_mode: Phase 3 - "hard" (strict) or "soft" (margins), default "hard"
            filter_margin_weight: Phase 3 - weight to blend filter compliance into score (0-1, default 0.1)
            soft_filter_limit_ratio: Phase 3 - trigger soft pass if hard results < limit * ratio (0-1, default 0.5)
            goal_mode: Phase 4 - "similarity" (default) or "plan" for non-overlapping schedule
            time_windows: Optional list of time windows used for filtering and plan mode
            min_break_minutes: Minimum required break between sessions in plan mode
            max_gap_minutes: Optional max allowed gap between planned sessions
            plan_candidate_multiplier: Candidate pool multiplier before plan optimization

        Returns:
            List of tuples: (session, scores_dict) where scores_dict contains:
            - overall_score (0-1): Reranked score using Phase 2 formula + Phase 3 compliance
            - semantic_similarity (0-1 or None): Query similarity from Chroma
            - liked_cluster_similarity (0-1 or None): Centroid similarity (Phase 2)
            - disliked_similarity (0-1 or None): Max similarity to disliked sessions (Phase 2)
            - filter_match_ratio (0-1): Matching filters / total active filters
            - filter_compliance_score (0-1 or None): Phase 3 compliance (soft pass only)
            - explanation (str): Human-readable summary

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If recommendation fails
        """
        # Normalize inputs
        accepted_ids = accepted_ids or []
        rejected_ids = rejected_ids or []
        seen_ids = set(accepted_ids + rejected_ids)
        is_plan_mode = goal_mode == "plan"
        candidate_limit = limit * plan_candidate_multiplier if is_plan_mode else limit

        try:
            # Path 3: CRUD Fallback (no query AND no accepted_ids)
            # Most efficient for filters-only queries
            if not query and not accepted_ids:
                active_filters = self._has_active_filters(
                    event_id=event_id,
                    session_format=session_format,
                    tags=tags,
                    location=location,
                    language=language,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    time_windows=time_windows,
                )
                logger.info(
                    "recommendation_crud_fallback",
                    rejected_ids_count=len(rejected_ids),
                    has_filters=active_filters,
                    goal_mode=goal_mode,
                )
                recommendations = await self._recommend_fallback(
                    db,
                    rejected_ids=rejected_ids,
                    event_id=event_id,
                    session_format=session_format,
                    tags=tags,
                    location=location,
                    language=language,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    time_windows=time_windows,
                    limit=candidate_limit,
                )
                return self._apply_plan_mode_if_needed(
                    recommendations=recommendations,
                    is_plan_mode=is_plan_mode,
                    limit=limit,
                    time_windows=time_windows,
                    min_break_minutes=min_break_minutes,
                    max_gap_minutes=max_gap_minutes,
                )

            # Path 1 & 2: Semantic Search (query OR accepted_ids provided)
            recommendations, search_debug = await self._recommend_with_semantic_search(
                db=db,
                query=query,
                accepted_ids=accepted_ids,
                rejected_ids=rejected_ids,
                seen_ids=seen_ids,
                candidate_limit=candidate_limit,
                event_id=event_id,
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_mode=filter_mode,
                filter_margin_weight=filter_margin_weight,
                soft_filter_limit_ratio=soft_filter_limit_ratio,
            )
            recommendations = self._apply_plan_mode_if_needed(
                recommendations=recommendations,
                is_plan_mode=is_plan_mode,
                limit=limit,
                time_windows=time_windows,
                min_break_minutes=min_break_minutes,
                max_gap_minutes=max_gap_minutes,
            )

            logger.info(
                "recommendation_completed",
                hard_pass_results=search_debug["hard_pass_results"],
                soft_pass_results=search_debug["soft_pass_results"],
                final_recommendations=len(recommendations),
                limit=limit,
                soft_pass_triggered=search_debug["soft_pass_triggered"],
                goal_mode=goal_mode,
            )

            return recommendations

        except InvalidEmbeddingTextError:
            raise
        except EmbeddingSearchError:
            raise
        except Exception as e:
            logger.error(
                "recommendation_failed",
                error=str(e),
                error_type=type(e).__name__,
                accepted_ids_count=len(accepted_ids),
                rejected_ids_count=len(rejected_ids),
            )
            raise EmbeddingSearchError(f"Recommendation failed: {e!s}") from e

    def _apply_plan_mode_if_needed(
        self,
        recommendations: list[tuple],
        is_plan_mode: bool,
        limit: int,
        time_windows: list[Any] | None,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> list[tuple]:
        """Apply Phase 4 plan optimization if goal mode requires it."""
        if not is_plan_mode:
            return recommendations
        return self._optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
        )

    @staticmethod
    def _extract_window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
        """Extract (start, end) from TimeWindow objects or plain dicts."""
        if isinstance(window, dict):
            return window.get("start"), window.get("end")
        return getattr(window, "start", None), getattr(window, "end", None)

    def _build_time_windows_chroma_condition(self, time_windows: list[Any] | None) -> dict | None:
        """Build Chroma OR condition for multiple time windows."""
        return self._build_time_windows_conditions(time_windows)

    async def _collect_soft_pass_candidates(
        self,
        query_embedding: list[float],
        soft_search_limit: int,
        nin_condition: dict | None,
        time_windows: list[Any] | None,
    ) -> list:
        """Collect soft-pass candidates, splitting requests by window when provided."""
        if not time_windows:
            chroma_where_soft = self._combine_conditions(nin_condition, None)
            if chroma_where_soft:
                return await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=soft_search_limit, where=chroma_where_soft
                )
            return await self.embedding_service.search_similar_sessions(
                query_embedding, limit=soft_search_limit
            )

        per_window_limit = max(1, soft_search_limit)
        collected_results = []
        for window in time_windows:
            window_condition = self._build_time_windows_chroma_condition([window])
            chroma_where_soft = self._combine_conditions(nin_condition, window_condition)
            if chroma_where_soft:
                results = await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=per_window_limit,
                    where=chroma_where_soft,
                )
            else:
                results = await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=per_window_limit,
                )
            collected_results.extend(results)

        # Keep highest similarity hit per session.
        deduped: dict[int, tuple[int, float, Any]] = {}
        for session_id, similarity, metadata in collected_results:
            current = deduped.get(session_id)
            if current is None or similarity > current[1]:
                deduped[session_id] = (session_id, similarity, metadata)
        return list(deduped.values())

    async def _recommend_with_semantic_search(
        self,
        db: Session,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
        seen_ids: set[int],
        candidate_limit: int,
        event_id: int | None,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_mode: str,
        filter_margin_weight: float,
        soft_filter_limit_ratio: float,
    ) -> tuple[list[tuple], dict[str, Any]]:
        """Run semantic recommendation path (query/centroid + two-pass search + reranking)."""
        query_embedding, semantic_similarity_enabled = await self._determine_query_embedding(
            query=query,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
        )

        metadata_conditions = self._build_chroma_conditions(
            session_format=session_format,
            tags=tags,
            location=location,
            language=language,
            duration_min=duration_min,
            duration_max=duration_max,
        )
        time_windows_condition = self._build_time_windows_chroma_condition(time_windows)
        metadata_conditions = self._combine_conditions(metadata_conditions, time_windows_condition)

        nin_condition = {"session_id": {"$nin": list(seen_ids)}} if seen_ids else None
        chroma_where = self._combine_conditions(nin_condition, metadata_conditions)

        try:
            if chroma_where:
                chroma_results_hard = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=candidate_limit, where=chroma_where
                )
            else:
                chroma_results_hard = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=candidate_limit
                )
        except Exception as e:
            logger.error("recommendation_chroma_search_failed", error=str(e))
            raise EmbeddingSearchError(f"Semantic search failed: {e!s}") from e

        chroma_results_soft = []
        soft_pass_triggered = False
        soft_filter_threshold = candidate_limit * soft_filter_limit_ratio

        if filter_mode == "soft" and len(chroma_results_hard) < soft_filter_threshold:
            soft_pass_triggered = True
            try:
                soft_search_limit = max(
                    candidate_limit * 2,
                    int(candidate_limit / soft_filter_limit_ratio),
                )
                chroma_results_soft = await self._collect_soft_pass_candidates(
                    query_embedding=query_embedding,
                    soft_search_limit=soft_search_limit,
                    nin_condition=nin_condition,
                    time_windows=time_windows,
                )
                logger.info(
                    "recommendation_soft_pass_enabled",
                    hard_results=len(chroma_results_hard),
                    soft_search_limit=soft_search_limit,
                    soft_results_before_dedup=len(chroma_results_soft),
                    soft_filter_threshold=soft_filter_threshold,
                )
            except Exception as e:
                logger.warning("recommendation_soft_pass_search_failed", error=str(e))
                chroma_results_soft = []

        preference_embeddings = await self._prefetch_preference_embeddings(
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
        )
        recommendations = await self._process_chroma_recommendations(
            chroma_results_hard=chroma_results_hard,
            chroma_results_soft=chroma_results_soft,
            db=db,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=preference_embeddings["liked"],
            disliked_embeddings=preference_embeddings["disliked"],
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            event_id=event_id,
            session_format=session_format,
            tags=tags,
            location=location,
            language=language,
            duration_min=duration_min,
            duration_max=duration_max,
            time_windows=time_windows,
            filter_margin_weight=filter_margin_weight,
            limit=candidate_limit,
            soft_pass_triggered=soft_pass_triggered,
        )

        return recommendations, {
            "hard_pass_results": len(chroma_results_hard),
            "soft_pass_results": len(chroma_results_soft),
            "soft_pass_triggered": soft_pass_triggered,
        }

    def _is_within_time_windows(
        self,
        session,
        time_windows: list[Any] | None,
    ) -> bool:
        """Check if session fits entirely inside any configured time window."""
        if not time_windows:
            return True

        for window in time_windows:
            start, end = self._extract_window_bounds(window)
            if start is None or end is None:
                continue
            if session.start_datetime >= start and session.end_datetime <= end:
                return True
        return False

    def _has_required_break(
        self,
        session,
        selected_session,
        min_break_minutes: int,
    ) -> bool:
        """Check if candidate keeps minimum break distance from an already selected session."""
        if min_break_minutes <= 0:
            return True

        if session.start_datetime >= selected_session.end_datetime:
            gap_minutes = (
                session.start_datetime - selected_session.end_datetime
            ).total_seconds() / 60
            return gap_minutes >= min_break_minutes

        if selected_session.start_datetime >= session.end_datetime:
            gap_minutes = (
                selected_session.start_datetime - session.end_datetime
            ).total_seconds() / 60
            return gap_minutes >= min_break_minutes

        return False

    def _fits_non_overlap_constraints(
        self,
        session,
        selected: list[tuple],
        min_break_minutes: int,
    ) -> bool:
        """Ensure candidate doesn't overlap selected sessions and satisfies break constraints."""
        for selected_session, _ in selected:
            if DateTimeUtils.get_datetime_range_overlap(
                session.start_datetime,
                session.end_datetime,
                selected_session.start_datetime,
                selected_session.end_datetime,
            ):
                return False
            if not self._has_required_break(session, selected_session, min_break_minutes):
                return False
        return True

    def _fits_gap_constraint(
        self,
        session,
        selected: list[tuple],
        max_gap_minutes: int | None,
    ) -> bool:
        """Keep selected sessions reasonably connected when max gap is configured."""
        if max_gap_minutes is None or not selected:
            return True

        min_gap_minutes = None
        for selected_session, _ in selected:
            if session.start_datetime >= selected_session.end_datetime:
                gap = (session.start_datetime - selected_session.end_datetime).total_seconds() / 60
            elif selected_session.start_datetime >= session.end_datetime:
                gap = (selected_session.start_datetime - session.end_datetime).total_seconds() / 60
            else:
                continue

            if min_gap_minutes is None or gap < min_gap_minutes:
                min_gap_minutes = gap

        if min_gap_minutes is None:
            return True
        return min_gap_minutes <= max_gap_minutes

    def _optimize_session_plan(
        self,
        recommendations: list[tuple],
        limit: int,
        time_windows: list[Any] | None,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> list[tuple]:
        """Select a non-overlapping recommendation plan using a deterministic greedy strategy."""
        if not recommendations:
            return []

        ranked_candidates = sorted(
            recommendations,
            key=lambda item: (
                -item[1]["overall_score"],
                item[0].start_datetime,
                item[0].end_datetime,
                item[0].id,
            ),
        )

        selected: list[tuple] = []
        for session, scores in ranked_candidates:
            if not self._is_within_time_windows(session, time_windows):
                continue
            if not self._fits_non_overlap_constraints(session, selected, min_break_minutes):
                continue
            if not self._fits_gap_constraint(session, selected, max_gap_minutes):
                continue

            plan_scores = dict(scores)
            base_explanation = plan_scores.get("explanation") or ""
            plan_scores["explanation"] = (
                f"{base_explanation}, plan-mode: non-overlap selected".strip(", ")
            )
            selected.append((session, plan_scores))

            if len(selected) >= limit:
                break

        selected.sort(key=lambda item: (item[0].start_datetime, item[0].id))
        return selected

    async def _process_chroma_recommendations(
        self,
        chroma_results_hard: list,
        chroma_results_soft: list,
        db: Session,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        event_id: int | None,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
        filter_margin_weight: float,
        limit: int,
        soft_pass_triggered: bool = False,
    ) -> list[tuple]:
        """Process Chroma search results into recommendations with Phase 2 & 3 re-ranking.

        Handles both hard-pass and soft-pass results:
        - Hard pass: Results strictly matching all filters
        - Soft pass: Near-matches when hard filters are too restrictive (Phase 3)

        Blends Phase 2 re-ranking (semantic + liked/disliked) with Phase 3 compliance scoring.

        Args:
            chroma_results_hard: Strict filter-matched results from hard pass
            chroma_results_soft: Near-match results from soft pass (if triggered)
            db: Database session
            semantic_similarity_enabled: Whether query-based search was used
            liked_embeddings: Prefetched liked session embeddings keyed by session ID
            disliked_embeddings: Prefetched disliked session embeddings keyed by session ID
            liked_embedding_weight: Weight to boost liked-session similarities
            disliked_embedding_weight: Weight to penalize disliked-session similarities
            event_id: Optional event filter
            session_format: Optional format filter
            tags: Optional tags filter
            location: Optional location filter
            language: Optional language filter
            duration_min: Optional minimum duration
            duration_max: Optional maximum duration
            time_windows: Optional list of window constraints
            filter_mode: "hard" (strict) or "soft" (margins)
            filter_margin_weight: Weight to blend compliance into score (Phase 3)
            limit: Max recommendations to return
            soft_pass_triggered: Whether soft pass was executed

        Returns:
            List of tuples: (session, scores_dict) with Phase 2 & 3 scores
        """
        # Collect embeddings and set up data structures
        chroma_id_to_embedding = await self._batch_fetch_embeddings(
            chroma_results_hard, chroma_results_soft
        )
        hard_pass_session_ids = {session_id for session_id, _, _ in chroma_results_hard}
        recommendations = []

        # Process hard pass results (strict filter matches)
        await self._process_hard_pass_results(
            chroma_results_hard=chroma_results_hard,
            db=db,
            event_id=event_id,
            chroma_id_to_embedding=chroma_id_to_embedding,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=liked_embeddings,
            disliked_embeddings=disliked_embeddings,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_margin_weight=filter_margin_weight,
            limit=limit,
            recommendations=recommendations,
        )

        # Process soft pass results (near-matches) if triggered
        if soft_pass_triggered and chroma_results_soft:
            await self._process_soft_pass_results(
                chroma_results_soft=chroma_results_soft,
                hard_pass_session_ids=hard_pass_session_ids,
                db=db,
                event_id=event_id,
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
                chroma_id_to_embedding=chroma_id_to_embedding,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_margin_weight=filter_margin_weight,
                limit=limit,
                recommendations=recommendations,
            )

        # Extract session and scores (removing source tag)
        recommendations_final = [(session, scores) for session, scores, _ in recommendations]

        # Sort by overall_score (highest first) - this is the re-ranked score
        recommendations_final.sort(key=lambda x: x[1]["overall_score"], reverse=True)

        # Return top limit results
        recommendations_final = recommendations_final[:limit]

        logger.info(
            "recommendation_completed",
            hard_pass_results=sum(1 for _, _, src in recommendations if src == "hard"),
            soft_pass_results=sum(1 for _, _, src in recommendations if src == "soft"),
            final_recommendations=len(recommendations_final),
            limit=limit,
            soft_pass_triggered=soft_pass_triggered,
        )

        return recommendations_final

    async def _determine_query_embedding(
        self,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> tuple[list, bool]:
        """Determine query embedding from text or liked-session centroid.

        Returns: (embedding_vector, semantic_similarity_enabled)
        """
        if query:
            if not EmbeddingService.validate_embedding_text(query):
                raise InvalidEmbeddingTextError("Query text is invalid or too long")
            query_embedding = await self.embedding_service.embed_query(query)
            logger.info(
                "recommendation_query_provided",
                query_length=len(query),
                accepted_ids_count=len(accepted_ids),
                rejected_ids_count=len(rejected_ids),
            )
            return query_embedding, True

        # Compute centroid from liked-session embeddings
        liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
        if not liked_embeddings:
            raise EmbeddingSearchError(f"No embeddings found for liked sessions: {accepted_ids}")

        import numpy as np

        embedding_vectors = list(liked_embeddings.values())
        # Ensure all elements are pure Python floats (not numpy scalars)
        centroid_array = np.mean(embedding_vectors, axis=0)
        query_embedding = [float(x) for x in centroid_array]

        logger.info(
            "recommendation_centroid_computed",
            accepted_ids_count=len(accepted_ids),
            embeddings_found=len(liked_embeddings),
            rejected_ids_count=len(rejected_ids),
        )
        return query_embedding, False

    async def _recommend_fallback(
        self,
        db: Session,
        rejected_ids: list[int],
        event_id: int | None = None,
        session_format: str | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        time_windows: list[Any] | None = None,
        limit: int = 10,
    ) -> list[tuple]:
        """
        Recommendation fallback using CRUD list_with_filters.

        Used when no semantic context (query, accepted_ids) is available.
        Efficiently filters using database queries, then excludes rejected IDs.

        Returns:
            List of tuples: (session, scores_dict) with basic metrics
        """
        try:
            # Use CRUD to fetch filtered sessions
            sessions = session_crud.list_with_filters(
                db=db,
                limit=limit + len(rejected_ids),  # Over-fetch to account for exclusions
                status=SessionStatus.PUBLISHED,
                event_id=event_id,
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
            )

            # Exclude rejected sessions and compute scores
            recommendations = []
            for session in sessions:
                # Skip rejected sessions
                if session.id in rejected_ids:
                    continue

                # Compute basic scores (no semantic data available)
                scores = {
                    "overall_score": 1.0,  # All matches are equally valid without semantic scoring
                    "semantic_similarity": None,
                    "liked_cluster_similarity": None,
                    "disliked_similarity": None,
                    "filter_match_ratio": 1.0,  # All sessions passed CRUD filter
                    "filter_compliance_score": None,  # Not computed for CRUD fallback
                    "explanation": "Matched all specified filters (no semantic query provided)",
                }

                recommendations.append((session, scores))

                if len(recommendations) >= limit:
                    break

            logger.info(
                "recommendation_crud_completed",
                rejected_ids_count=len(rejected_ids),
                recommendations=len(recommendations),
                limit=limit,
            )

            return recommendations

        except Exception as e:
            logger.error(
                "recommendation_crud_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmbeddingSearchError(f"CRUD recommendation fallback failed: {e!s}") from e

    async def _prefetch_preference_embeddings(
        self,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> dict[str, dict[int, list[float]]]:
        """Fetch liked/disliked embeddings once per request for reuse during scoring."""
        liked_embeddings: dict[int, list[float]] = {}
        disliked_embeddings: dict[int, list[float]] = {}

        if accepted_ids:
            try:
                liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
            except Exception as e:
                logger.warning(
                    "liked_embeddings_prefetch_failed",
                    error=str(e),
                    accepted_ids_count=len(accepted_ids),
                )

        if rejected_ids:
            try:
                disliked_embeddings = await self.embedding_service.get_session_embeddings(
                    rejected_ids
                )
            except Exception as e:
                logger.warning(
                    "disliked_embeddings_prefetch_failed",
                    error=str(e),
                    rejected_ids_count=len(rejected_ids),
                )

        return {
            "liked": liked_embeddings,
            "disliked": disliked_embeddings,
        }

    def _get_default_embedding(self) -> list[float]:
        """Build a zero-vector fallback matching configured embedding dimensions."""
        return [0.0] * self.embedding_service.embedding_dimension

    async def _batch_fetch_embeddings(
        self,
        chroma_results_hard: list,
        chroma_results_soft: list,
    ) -> dict:
        """Batch fetch embeddings for all session IDs from hard and soft results."""
        all_session_ids = []
        for session_id, _, _ in chroma_results_hard:
            all_session_ids.append(session_id)
        for session_id, _, _ in chroma_results_soft:
            all_session_ids.append(session_id)

        chroma_id_to_embedding = {}
        if all_session_ids:
            try:
                embeddings_dict = await self.embedding_service.get_session_embeddings(
                    all_session_ids
                )
                for session_id, embedding in embeddings_dict.items():
                    chroma_id_to_embedding[f"session_{session_id}"] = embedding
            except Exception as e:
                logger.warning(
                    "batch_embedding_retrieval_failed",
                    error=str(e),
                    sessions_count=len(all_session_ids),
                )
        return chroma_id_to_embedding

    async def _process_hard_pass_results(
        self,
        chroma_results_hard: list,
        db: Session,
        event_id: int | None,
        chroma_id_to_embedding: dict,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_margin_weight: float,
        limit: int,
        recommendations: list,
    ) -> None:
        """Process hard pass results (strict filter matches)."""
        for session_id, chroma_similarity, _ in chroma_results_hard:
            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue

            if event_id and session.event_id != event_id:
                continue

            chroma_id = f"session_{session_id}"
            session_embedding = chroma_id_to_embedding.get(chroma_id)

            if session_embedding is None:
                logger.warning("session_embedding_not_found", session_id=session_id)
                session_embedding = self._get_default_embedding()

            filter_compliance_score = 1.0

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_compliance_score=filter_compliance_score,
                filter_margin_weight=filter_margin_weight,
            )

            recommendations.append((session, scores, "hard"))

            if len(recommendations) >= limit:
                break

    async def _process_soft_pass_results(
        self,
        chroma_results_soft: list,
        hard_pass_session_ids: set,
        db: Session,
        event_id: int | None,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
        chroma_id_to_embedding: dict,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_margin_weight: float,
        limit: int,
        recommendations: list,
    ) -> None:
        """Process soft pass results (near-matches)."""
        for session_id, chroma_similarity, _ in chroma_results_soft:
            if session_id in hard_pass_session_ids:
                continue

            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue

            if event_id and session.event_id != event_id:
                continue

            if any(rec[0].id == session_id for rec in recommendations):
                continue

            chroma_id = f"session_{session_id}"
            session_embedding = chroma_id_to_embedding.get(chroma_id)

            if session_embedding is None:
                logger.warning("session_embedding_not_found_soft_pass", session_id=session_id)
                session_embedding = self._get_default_embedding()

            filter_compliance_score = self._compute_filter_compliance_score(
                session=session,
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
            )

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_compliance_score=filter_compliance_score,
                filter_margin_weight=filter_margin_weight,
            )

            recommendations.append((session, scores, "soft"))

            if len(recommendations) >= limit:
                break

    def _check_format(self, session, session_format: str | None) -> bool:
        """Check if session format matches filter."""
        if session_format is None:
            return False
        return session.session_format and session.session_format.value == session_format

    def _check_language(self, session, language: str | None) -> bool:
        """Check if session language matches filter."""
        if language is None:
            return False
        return session.language == language

    def _check_tags(self, session, tags: list[str] | None) -> bool:
        """Check if session has matching tags (OR logic)."""
        if not tags:
            return False
        session_tags_set = set(session.tags or [])
        return any(tag in session_tags_set for tag in tags)

    def _check_location(self, session, location: list[str] | None) -> bool:
        """Check if session location matches (OR logic)."""
        if not location:
            return False
        return session.location and any(loc in session.location for loc in location)

    def _check_duration_min(self, session, duration_min: int | None) -> bool:
        """Check if session meets minimum duration."""
        if duration_min is None:
            return False
        return session.duration and session.duration >= duration_min

    def _check_duration_max(self, session, duration_max: int | None) -> bool:
        """Check if session stays within maximum duration."""
        if duration_max is None:
            return False
        return session.duration and session.duration <= duration_max

    def _check_time_windows(self, session, time_windows: list[Any] | None) -> bool:
        """Check whether a session fits within any configured time window."""
        if not time_windows:
            return False
        return self._is_within_time_windows(session, time_windows)

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """
        Compute cosine similarity between two vectors (0-1 range).

        Uses numpy for efficient computation. Maps cosine similarity from [-1, 1] to [0, 1].

        Args:
            vec1: First embedding vector
            vec2: Second embedding vector

        Returns:
            Cosine similarity score (0-1)
        """
        import numpy as np

        # Ensure vectors are numpy arrays and flatten to 1D
        v1 = np.asarray(vec1, dtype=np.float32).flatten()
        v2 = np.asarray(vec2, dtype=np.float32).flatten()

        # Compute cosine similarity: dot(v1, v2) / (||v1|| * ||v2||)
        dot_product = float(np.dot(v1, v2))
        norm_v1 = float(np.linalg.norm(v1))
        norm_v2 = float(np.linalg.norm(v2))

        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0

        cosine_sim = dot_product / (norm_v1 * norm_v2)
        # Map from [-1, 1] to [0, 1]: (sim + 1) / 2
        return float(max(0.0, (cosine_sim + 1.0) / 2.0))

    def _compute_filter_compliance_score(
        self,
        session,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
    ) -> float:
        """
        Compute filter compliance score for a session (0-1).

        Calculates what fraction of active filters the session matches.
        Used in Phase 3 (soft filter margins) to score near-matches.

        Example: With `language=en`, `format=workshop`, `duration_max=60`:
        - Session A: English + workshop + 45min  → 3/3 = 1.0 (perfect)
        - Session B: English + talk + 50min      → 1/3 = 0.33 (language only)

        Args:
            session: Session model instance
            session_format: Format to check
            tags: Tags to check (matches if any tag in session matches any in list)
            location: Locations to check (matches if any location matches)
            language: Language to check
            duration_min/max: Duration bounds to check
            time_windows: Time windows to check

        Returns:
            Compliance score (0-1), where 1.0 means all filters matched
        """
        filter_checks = [
            (session_format is not None, self._check_format(session, session_format)),
            (language is not None, self._check_language(session, language)),
            (tags is not None, self._check_tags(session, tags)),
            (location is not None, self._check_location(session, location)),
            (duration_min is not None, self._check_duration_min(session, duration_min)),
            (duration_max is not None, self._check_duration_max(session, duration_max)),
            (time_windows is not None, self._check_time_windows(session, time_windows)),
        ]

        matched = sum(check for is_active, check in filter_checks if is_active)
        total = sum(1 for is_active, _ in filter_checks if is_active)

        return 1.0 if total == 0 else matched / total

    def _compute_liked_similarity(
        self,
        session_embedding: list[float],
        liked_embeddings: dict[int, list[float]],
    ) -> float | None:
        """Compute centroid similarity from prefetched liked session embeddings."""
        if not liked_embeddings:
            return None

        try:
            import numpy as np

            centroid = np.mean(list(liked_embeddings.values()), axis=0).tolist()
            return self._cosine_similarity(session_embedding, centroid)
        except Exception as e:
            logger.warning(
                "liked_cluster_similarity_computation_failed",
                error=str(e),
                embeddings_count=len(liked_embeddings),
            )
            return None

    def _compute_disliked_similarity(
        self,
        session_embedding: list[float],
        disliked_embeddings: dict[int, list[float]],
    ) -> float | None:
        """Compute max similarity to prefetched disliked session embeddings."""
        if not disliked_embeddings:
            return None

        try:
            disliked_sims = [
                self._cosine_similarity(session_embedding, disliked_emb)
                for disliked_emb in disliked_embeddings.values()
            ]
            return max(disliked_sims) if disliked_sims else None
        except Exception as e:
            logger.warning(
                "disliked_similarity_computation_failed",
                error=str(e),
                embeddings_count=len(disliked_embeddings),
            )
            return None

    def _build_score_components(
        self,
        semantic_sim: float | None,
        liked_cluster_sim: float | None,
        disliked_sim: float | None,
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_compliance_score: float | None,
        filter_margin_weight: float,
    ) -> tuple[list, list, list]:
        """Build score components, weights, and explanation parts."""
        components = []
        weights = []
        explanations = []

        if semantic_sim is not None:
            components.append(semantic_sim)
            weights.append(1.0)
            explanations.append(f"semantic: {semantic_sim:.3f}")

        if liked_cluster_sim is not None and liked_embedding_weight > 0:
            components.append(liked_cluster_sim)
            weights.append(liked_embedding_weight)
            explanations.append(
                f"liked-cluster: {liked_cluster_sim:.3f} (weight: {liked_embedding_weight:.1f})"
            )

        if disliked_sim is not None and disliked_embedding_weight > 0:
            inverted_disliked = 1.0 - disliked_sim
            components.append(inverted_disliked)
            weights.append(disliked_embedding_weight)
            explanations.append(
                f"disliked-penalty: {disliked_sim:.3f} (weight: {disliked_embedding_weight:.1f})"
            )

        if filter_compliance_score is not None and filter_margin_weight > 0:
            components.append(filter_compliance_score)
            weights.append(filter_margin_weight)
            explanations.append(
                f"filter-compliance: {filter_compliance_score:.3f} (weight: {filter_margin_weight:.1f})"
            )

        return components, weights, explanations

    @staticmethod
    def _calculate_overall_score(components: list, weights: list) -> float:
        """Calculate overall score from components and weights."""
        if not components:
            return 0.5

        weighted_sum = sum(c * w for c, w in zip(components, weights, strict=False))
        total_weight = sum(weights)
        overall_score = weighted_sum / total_weight if total_weight > 0 else 0.5

        return max(0.0, min(1.0, overall_score))

    async def _compute_recommendation_scores(
        self,
        session_embedding: list[float],
        chroma_similarity: float,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
        filter_compliance_score: float | None = None,
        filter_margin_weight: float = 0.1,
    ) -> dict:
        """
        Compute multi-dimensional recommendation scores with Phase 2 & 3 features.

        **Phase 2 Features:**
        - Actual centroid similarity: compute from liked session embeddings
        - Actual disliked penalty: compute max similarity to disliked embeddings
        - Re-ranking formula: weighted average that adapts to available scores
          - overall_score dynamically combines semantic_sim, liked_sim, disliked_sim
          - Result always in [0, 1] range with proper differentiation

        **Phase 3 Features:**
        - Filter compliance scoring: how well session matches requested filters
        - Soft filter margins: blend compliance into overall score to expand candidates
          - filter_margin_weight controls compliance contribution
          - Enables near-matches to rank higher than hard cutoff

        Args:
            session_embedding: Embedding vector for the result session
            chroma_similarity: Normalized similarity from Chroma (0-1)
            semantic_similarity_enabled: Whether query-based search was used
            liked_embeddings: Prefetched liked session embeddings
            disliked_embeddings: Prefetched disliked session embeddings
            liked_embedding_weight: Weight (a) to boost liked-session similarity
            disliked_embedding_weight: Weight (b) to penalize disliked-session similarity
            filter_compliance_score: Phase 3 - ratio of matched filters (0-1)
            filter_margin_weight: Phase 3 - weight to blend compliance into score

        Returns:
            Dict with overall_score, component scores, and explanation
        """
        # Compute component scores
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None
        liked_cluster_sim = self._compute_liked_similarity(session_embedding, liked_embeddings)
        disliked_sim = self._compute_disliked_similarity(session_embedding, disliked_embeddings)

        # Build score components and weights
        score_components, score_weights, explanation_parts = self._build_score_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_compliance_score=filter_compliance_score,
            filter_margin_weight=filter_margin_weight,
        )

        # Calculate overall score
        overall_score = self._calculate_overall_score(score_components, score_weights)

        explanation = ", ".join(explanation_parts) if explanation_parts else "Matched filters"

        return {
            "overall_score": round(overall_score, 3),
            "semantic_similarity": round(semantic_sim, 3) if semantic_sim is not None else None,
            "liked_cluster_similarity": (
                round(liked_cluster_sim, 3) if liked_cluster_sim is not None else None
            ),
            "disliked_similarity": round(disliked_sim, 3) if disliked_sim is not None else None,
            "filter_match_ratio": 1.0,
            "filter_compliance_score": (
                round(filter_compliance_score, 3) if filter_compliance_score is not None else None
            ),
            "explanation": explanation,
        }
