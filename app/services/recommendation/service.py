"""Recommendation orchestration service.

Keeps recommendation flow and ranking logic isolated from search-only services.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, ClassVar

import structlog
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.services.embedding.exceptions import EmbeddingSearchError, InvalidEmbeddingTextError
from app.services.embedding.service import EmbeddingService
from app.services.recommendation.diversity import RecommendationDiversityOptimizer
from app.services.recommendation.filters import RecommendationFilterEvaluator
from app.services.recommendation.planning import RecommendationPlanner
from app.services.recommendation.scoring import RecommendationScoreEngine

logger = structlog.get_logger()


@dataclass(slots=True)
class RecommendationQueryParams:
    """Shared recommendation query/filter parameters across recommendation modes."""

    query: str | list[str] | None
    accepted_ids: list[int]
    rejected_ids: list[int]
    event_id: int | None = None
    session_format: list[str] | None = None
    tags: list[str] | None = None
    location_cities: list[str] | None = None
    location_names: list[str] | None = None
    language: list[str] | None = None
    duration_min: int | None = None
    duration_max: int | None = None
    liked_embedding_weight: float = 0.3
    disliked_embedding_weight: float = 0.2
    soft_filters: list[str] | None = None
    filter_margin_weight: float = 0.5
    diversity_weight: float = 0.0
    time_windows: list[Any] | None = None


class RecommendationService:
    """Coordinates recommendation execution paths and filter-mode semantic search."""

    SOFT_FILTER_KEYS: ClassVar[set[str]] = {
        "session_format",
        "tags",
        "location",
        "language",
        "duration",
    }

    @staticmethod
    def _normalize_query_list(query: str | list[str] | None) -> list[str]:
        """Normalize single/multi-query input to a trimmed unique list."""
        if query is None:
            return []

        values = query if isinstance(query, list) else [query]
        normalized: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            if text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _dedupe_chroma_results_by_similarity(chroma_results: list[tuple]) -> list[tuple]:
        """Keep only the highest-similarity hit per session across search passes."""
        deduped: dict[int, tuple[int, float, Any]] = {}
        for session_id, similarity, metadata in chroma_results:
            current = deduped.get(session_id)
            if current is None or similarity > current[1]:
                deduped[session_id] = (session_id, similarity, metadata)
        results = list(deduped.values())
        results.sort(key=lambda item: item[1], reverse=True)
        return results

    def __init__(self, embedding_service: EmbeddingService):
        self.embedding_service = embedding_service
        self.recommendation_planner = RecommendationPlanner()
        self.filter_evaluator = RecommendationFilterEvaluator(self.recommendation_planner)
        self.score_engine = RecommendationScoreEngine()
        self.diversity_optimizer = RecommendationDiversityOptimizer()

    @staticmethod
    def _extract_window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
        """Extract (start, end) from TimeWindow objects or plain dicts."""
        if isinstance(window, dict):
            return window.get("start"), window.get("end")
        return getattr(window, "start", None), getattr(window, "end", None)

    def _build_location_condition(
        self, location_cities: list[str] | None, location_names: list[str] | None
    ) -> dict | None:
        conditions = []
        if location_cities:
            conditions.extend([{"location_city": city} for city in location_cities])
        if location_names:
            conditions.extend([{"location_name": name} for name in location_names])
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$or": conditions}

    def _build_tags_condition(self, tags: list[str] | None) -> dict | None:
        if not tags:
            return None
        tag_conditions = [{"tags": {"$contains": tag}} for tag in tags]
        if len(tag_conditions) == 1:
            return tag_conditions[0]
        return {"$or": tag_conditions}

    def _build_time_windows_conditions(self, time_windows: list[Any] | None) -> dict | None:
        if not time_windows:
            return None

        window_conditions: list[dict[str, Any]] = []
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

    @staticmethod
    def _build_or_equals_condition(field: str, values: list[str] | None) -> dict | None:
        if not values:
            return None
        if len(values) == 1:
            return {field: values[0]}
        return {"$or": [{field: value} for value in values]}

    @staticmethod
    def _build_simple_conditions(
        session_format: list[str] | None,
        language: list[str] | None,
        duration_min: int | None,
        duration_max: int | None,
    ) -> list[dict]:
        conditions = []

        session_format_condition = RecommendationService._build_or_equals_condition(
            "session_format", session_format
        )
        if session_format_condition:
            conditions.append(session_format_condition)

        language_condition = RecommendationService._build_or_equals_condition("language", language)
        if language_condition:
            conditions.append(language_condition)

        if duration_min is not None:
            conditions.append({"duration": {"$gte": duration_min}})
        if duration_max is not None:
            conditions.append({"duration": {"$lte": duration_max}})
        return conditions

    def _build_chroma_conditions(
        self,
        event_id: int | None = None,
        seen_ids: set[int] | None = None,
        session_format: list[str] | None = None,
        tags: list[str] | None = None,
        location_cities: list[str] | None = None,
        location_names: list[str] | None = None,
        language: list[str] | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        time_windows: list[Any] | None = None,
    ) -> dict | None:
        all_conditions = []

        if event_id is not None:
            all_conditions.append({"event_id": event_id})

        if seen_ids:
            all_conditions.append({"session_id": {"$nin": list(seen_ids)}})

        all_conditions.extend(
            self._build_simple_conditions(session_format, language, duration_min, duration_max)
        )

        location_condition = self._build_location_condition(location_cities, location_names)
        if location_condition:
            all_conditions.append(location_condition)

        tags_condition = self._build_tags_condition(tags)
        if tags_condition:
            all_conditions.append(tags_condition)

        time_window_condition = self._build_time_windows_conditions(time_windows)
        if time_window_condition:
            all_conditions.append(time_window_condition)

        if not all_conditions:
            return None
        if len(all_conditions) == 1:
            return all_conditions[0]
        return {"$and": all_conditions}

    @classmethod
    def _get_soft_filter_set(cls, soft_filters: list[str] | None) -> set[str]:
        if not soft_filters:
            return set()
        return set(soft_filters).intersection(cls.SOFT_FILTER_KEYS)

    def _optimize_session_plan(
        self,
        recommendations: list[tuple],
        limit: int,
        time_windows: list[Any] | None,
        min_break_minutes: int,
        max_gap_minutes: int | None,
        diversity_scores: dict[int, float] | None = None,
        diversity_weight: float = 0.0,
    ) -> list[tuple]:
        return self.recommendation_planner.optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
            diversity_scores=diversity_scores,
            diversity_weight=diversity_weight,
        )

    @staticmethod
    def _build_min_gap_delta(max_gap_minutes: int | None) -> timedelta | None:
        return timedelta(minutes=max_gap_minutes) if max_gap_minutes is not None else None

    @staticmethod
    def _append_gap_window_if_allowed(
        gap_windows: list[dict[str, datetime]],
        start: datetime | None,
        end: datetime | None,
        min_gap_delta: timedelta | None,
    ) -> None:
        if start is None or end is None or end <= start:
            return
        gap_delta = end - start
        if min_gap_delta is not None and gap_delta <= min_gap_delta:
            return
        gap_windows.append({"start": start, "end": end})

    @staticmethod
    def _sort_planned_sessions(planned: list[tuple]) -> list[Any]:
        return sorted(
            [session for session, _ in planned],
            key=lambda session: (session.start_datetime, session.end_datetime, session.id),
        )

    @staticmethod
    def _window_overlaps_for_sessions(
        sorted_sessions: list[Any],
        window_start: datetime,
        window_end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        overlaps: list[tuple[datetime, datetime]] = []
        for session in sorted_sessions:
            occupied_start = max(session.start_datetime, window_start)
            occupied_end = min(session.end_datetime, window_end)
            if occupied_end <= occupied_start:
                continue
            overlaps.append((occupied_start, occupied_end))

        overlaps.sort(key=lambda item: (item[0], item[1]))
        return overlaps

    def _derive_gaps_from_time_windows(
        self,
        sorted_sessions: list[Any],
        time_windows: list[Any],
        min_gap_delta: timedelta | None,
    ) -> list[dict[str, datetime]]:
        gap_windows: list[dict[str, datetime]] = []
        for window in time_windows:
            window_start, window_end = self._extract_window_bounds(window)
            if window_start is None or window_end is None or window_end <= window_start:
                continue

            cursor = window_start
            overlaps = self._window_overlaps_for_sessions(
                sorted_sessions=sorted_sessions,
                window_start=window_start,
                window_end=window_end,
            )
            for occupied_start, occupied_end in overlaps:
                self._append_gap_window_if_allowed(
                    gap_windows=gap_windows,
                    start=cursor,
                    end=occupied_start,
                    min_gap_delta=min_gap_delta,
                )
                if occupied_end > cursor:
                    cursor = occupied_end
                if cursor >= window_end:
                    break

            self._append_gap_window_if_allowed(
                gap_windows=gap_windows,
                start=cursor,
                end=window_end,
                min_gap_delta=min_gap_delta,
            )

        return gap_windows

    def _derive_internal_gaps(
        self,
        sorted_sessions: list[Any],
        min_gap_delta: timedelta | None,
    ) -> list[dict[str, datetime]]:
        if len(sorted_sessions) < 2:
            return []

        gap_windows: list[dict[str, datetime]] = []
        for index in range(len(sorted_sessions) - 1):
            self._append_gap_window_if_allowed(
                gap_windows=gap_windows,
                start=sorted_sessions[index].end_datetime,
                end=sorted_sessions[index + 1].start_datetime,
                min_gap_delta=min_gap_delta,
            )
        return gap_windows

    def _derive_gap_fill_windows(
        self,
        planned: list[tuple],
        time_windows: list[Any] | None,
        max_gap_minutes: int | None,
    ) -> list[dict[str, datetime]]:
        min_gap_delta = self._build_min_gap_delta(max_gap_minutes)
        sorted_sessions = self._sort_planned_sessions(planned)

        if time_windows:
            return self._derive_gaps_from_time_windows(
                sorted_sessions=sorted_sessions,
                time_windows=time_windows,
                min_gap_delta=min_gap_delta,
            )

        return self._derive_internal_gaps(
            sorted_sessions=sorted_sessions,
            min_gap_delta=min_gap_delta,
        )

    async def _collect_base_recommendations(
        self,
        db: Session,
        params: RecommendationQueryParams,
        seen_ids: set[int],
        candidate_limit: int,
    ) -> tuple[list[tuple], dict[str, Any]]:
        if not params.query and not params.accepted_ids:
            recommendations = await self._recommend_fallback(
                db,
                params=params,
                limit=candidate_limit,
            )
            return recommendations, {
                "hard_pass_results": 0,
                "soft_pass_results": 0,
                "soft_pass_triggered": False,
                "embeddings_map": {},
            }

        return await self._recommend_with_semantic_search(
            db=db,
            params=params,
            seen_ids=seen_ids,
            candidate_limit=candidate_limit,
        )

    async def _recommend_plan_mode(
        self,
        db: Session,
        params: RecommendationQueryParams,
        seen_ids: set[int],
        limit: int,
        plan_candidate_multiplier: int,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> tuple[list[tuple], dict[str, Any]]:
        candidate_limit = limit * plan_candidate_multiplier
        recommendations, search_debug = await self._collect_base_recommendations(
            db=db,
            params=params,
            seen_ids=seen_ids,
            candidate_limit=candidate_limit,
        )

        # First pass: optimize without diversity (pure relevance ranking)
        planned = self._optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=params.time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
        )

        gap_fill_windows = self._derive_gap_fill_windows(
            planned=planned,
            time_windows=params.time_windows,
            max_gap_minutes=max_gap_minutes,
        )
        if not gap_fill_windows:
            logger.info(
                "recommendation_plan_gap_fill_skipped_no_oversized_gaps",
                planned_size=len(planned),
                requested_limit=limit,
                max_gap_minutes=max_gap_minutes,
            )
            return planned, search_debug

        existing_ids = [session.id for session, _ in recommendations]
        planned_ids = [session.id for session, _ in planned]
        gap_fill_rejected = list(dict.fromkeys(params.rejected_ids + existing_ids + planned_ids))
        gap_fill_limit = max(limit * plan_candidate_multiplier, limit)

        gap_fill_params = replace(
            params,
            rejected_ids=gap_fill_rejected,
            time_windows=gap_fill_windows,
        )
        gap_fill_candidates = await self._recommend_fallback(
            db=db,
            params=gap_fill_params,
            limit=gap_fill_limit,
        )
        if not gap_fill_candidates:
            return planned, search_debug

        merged_by_session_id: dict[int, tuple] = {}
        for session, scores in recommendations + gap_fill_candidates:
            current = merged_by_session_id.get(session.id)
            if current is None or scores["overall_score"] > current[1]["overall_score"]:
                merged_by_session_id[session.id] = (session, scores)

        # Compute diversity scores for merged candidates for use in second planning pass
        # This single pass on merged candidates ensures gap-fill selections are diverse wrt planned sessions
        diversity_scores: dict[int, float] | None = None
        if params.diversity_weight > 0:
            merged_candidates = list(merged_by_session_id.values())
            reranked = self.diversity_optimizer.diversify_results(
                candidates=merged_candidates,
                limit=len(merged_candidates),
                diversity_weight=params.diversity_weight,
                embeddings_map=search_debug.get("embeddings_map"),
                session_format=params.session_format,
                tags=params.tags,
                language=params.language,
            )
            # Extract diversity scores from reranked results
            diversity_scores = {}
            for session, scores in reranked:
                if scores.get("diversity_score") is not None:
                    diversity_scores[session.id] = scores["diversity_score"]

        replanned = self._optimize_session_plan(
            recommendations=list(merged_by_session_id.values()),
            limit=limit,
            time_windows=params.time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
            diversity_scores=diversity_scores,
            diversity_weight=params.diversity_weight,
        )

        logger.info(
            "recommendation_plan_gap_fill_completed",
            initial_plan_size=len(planned),
            gap_fill_candidates=len(gap_fill_candidates),
            final_plan_size=len(replanned),
            requested_limit=limit,
        )
        return replanned, search_debug

    async def _recommend_default_mode(
        self,
        db: Session,
        params: RecommendationQueryParams,
        seen_ids: set[int],
        limit: int,
    ) -> tuple[list[tuple], dict[str, Any]]:
        return await self._collect_base_recommendations(
            db=db,
            params=params,
            seen_ids=seen_ids,
            candidate_limit=limit,
        )

    async def recommend_sessions(
        self,
        db: Session,
        accepted_ids: list[int] | None = None,
        rejected_ids: list[int] | None = None,
        query: str | list[str] | None = None,
        limit: int = 10,
        event_id: int | None = None,
        session_format: list[str] | None = None,
        tags: list[str] | None = None,
        location_cities: list[str] | None = None,
        location_names: list[str] | None = None,
        language: list[str] | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
        soft_filters: list[str] | None = None,
        filter_margin_weight: float = 0.5,
        diversity_weight: float = 0.3,
        goal_mode: str = "similarity",
        time_windows: list[Any] | None = None,
        min_break_minutes: int = 0,
        max_gap_minutes: int | None = None,
        plan_candidate_multiplier: int = 2,
    ) -> list[tuple]:
        accepted_ids = accepted_ids or []
        rejected_ids = rejected_ids or []
        params = RecommendationQueryParams(
            query=query,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
            event_id=event_id,
            session_format=session_format,
            tags=tags,
            location_cities=location_cities,
            location_names=location_names,
            language=language,
            duration_min=duration_min,
            duration_max=duration_max,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            soft_filters=soft_filters,
            filter_margin_weight=filter_margin_weight,
            diversity_weight=diversity_weight,
            time_windows=time_windows,
        )
        seen_ids = set(params.accepted_ids + params.rejected_ids)

        try:
            if goal_mode == "plan":
                recommendations, search_debug = await self._recommend_plan_mode(
                    db=db,
                    params=params,
                    seen_ids=seen_ids,
                    limit=limit,
                    plan_candidate_multiplier=plan_candidate_multiplier,
                    min_break_minutes=min_break_minutes,
                    max_gap_minutes=max_gap_minutes,
                )
            else:
                recommendations, search_debug = await self._recommend_default_mode(
                    db=db,
                    params=params,
                    seen_ids=seen_ids,
                    limit=limit,
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

    async def _recommend_with_semantic_search(
        self,
        db: Session,
        params: RecommendationQueryParams,
        seen_ids: set[int],
        candidate_limit: int,
    ) -> tuple[list[tuple], dict[str, Any]]:
        query_embeddings, semantic_similarity_enabled = await self._determine_query_embeddings(
            query=params.query,
            accepted_ids=params.accepted_ids,
            rejected_ids=params.rejected_ids,
        )

        soft = self._get_soft_filter_set(params.soft_filters)
        has_soft = bool(soft)
        search_limit = candidate_limit * 2 if has_soft else candidate_limit

        where_condition = self._build_chroma_conditions(
            event_id=params.event_id,
            seen_ids=seen_ids,
            session_format=None if "session_format" in soft else params.session_format,
            tags=None if "tags" in soft else params.tags,
            location_cities=None if "location" in soft else params.location_cities,
            location_names=None if "location" in soft else params.location_names,
            language=None if "language" in soft else params.language,
            duration_min=None if "duration" in soft else params.duration_min,
            duration_max=None if "duration" in soft else params.duration_max,
            time_windows=params.time_windows,
        )

        chroma_results: list[tuple] = []
        for query_embedding in query_embeddings:
            try:
                if where_condition:
                    results = await self.embedding_service.search_similar_sessions(
                        query_embedding,
                        limit=search_limit,
                        where=where_condition,
                    )
                else:
                    results = await self.embedding_service.search_similar_sessions(
                        query_embedding,
                        limit=search_limit,
                    )
                chroma_results.extend(results)
            except Exception as e:
                logger.error("recommendation_chroma_search_failed", error=str(e))
                raise EmbeddingSearchError(f"Semantic search failed: {e!s}") from e

        if len(query_embeddings) > 1:
            chroma_results = self._dedupe_chroma_results_by_similarity(chroma_results)[
                :search_limit
            ]

        if has_soft:
            logger.info(
                "recommendation_soft_filters_active",
                soft_filters=list(soft),
                search_limit=search_limit,
                chroma_results_count=len(chroma_results),
                query_count=len(query_embeddings),
            )

        preference_embeddings = await self._prefetch_preference_embeddings(
            accepted_ids=params.accepted_ids,
            rejected_ids=params.rejected_ids,
        )
        recommendations, chroma_id_to_embedding = await self._process_chroma_recommendations(
            chroma_results=chroma_results,
            db=db,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=preference_embeddings["liked"],
            disliked_embeddings=preference_embeddings["disliked"],
            params=params,
            limit=candidate_limit,
        )

        return recommendations, {
            "hard_pass_results": 0 if has_soft else len(chroma_results),
            "soft_pass_results": len(chroma_results) if has_soft else 0,
            "soft_pass_triggered": has_soft,
            "embeddings_map": chroma_id_to_embedding,
        }

    async def _determine_query_embeddings(
        self,
        query: str | list[str] | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> tuple[list[list[float]], bool]:
        normalized_queries = self._normalize_query_list(query)
        if normalized_queries:
            for query_text in normalized_queries:
                if not EmbeddingService.validate_embedding_text(query_text):
                    raise InvalidEmbeddingTextError("Invalid query text for embedding")
            query_embeddings = [
                await self.embedding_service.embed_query(query_text)
                for query_text in normalized_queries
            ]
            return query_embeddings, True

        embedding, semantic_enabled = await self._determine_query_embedding(
            query=None,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
        )
        return [embedding], semantic_enabled

    async def _determine_query_embedding(
        self,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> tuple[list[float], bool]:
        rejected_ids_count = len(rejected_ids)

        if query:
            if not EmbeddingService.validate_embedding_text(query):
                raise InvalidEmbeddingTextError("Invalid query text for embedding")
            return await self.embedding_service.embed_query(query), True

        if accepted_ids:
            accepted_embeddings_map = await self.embedding_service.get_session_embeddings(
                accepted_ids
            )
            accepted_embeddings = list(accepted_embeddings_map.values())
            if not accepted_embeddings:
                logger.warning(
                    "accepted_embeddings_not_found",
                    accepted_ids_count=len(accepted_ids),
                    rejected_ids_count=rejected_ids_count,
                )
                raise EmbeddingSearchError("No embeddings found for accepted sessions")

            dim = len(accepted_embeddings[0])
            centroid = [0.0] * dim
            for emb in accepted_embeddings:
                for i, value in enumerate(emb):
                    centroid[i] += value
            centroid = [value / len(accepted_embeddings) for value in centroid]
            return centroid, False

        return self._get_default_embedding(), False

    async def _prefetch_preference_embeddings(
        self,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> dict[str, dict[int, list[float]]]:
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

        return {"liked": liked_embeddings, "disliked": disliked_embeddings}

    def _get_default_embedding(self) -> list[float]:
        return [0.0] * self.embedding_service.embedding_dimension

    async def _batch_fetch_embeddings(
        self,
        chroma_results: list,
    ) -> dict:
        all_session_ids = list(dict.fromkeys([session_id for session_id, _, _ in chroma_results]))

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

    def _compute_soft_filter_compliance(
        self,
        session: Any,
        params: RecommendationQueryParams,
    ) -> float | None:
        """Compute compliance only for soft_filter attributes. Returns None if no soft filters active."""
        soft = self._get_soft_filter_set(params.soft_filters)
        if not soft:
            return None

        return self.filter_evaluator.compute_filter_compliance_score(
            session=session,
            session_format=params.session_format if "session_format" in soft else None,
            tags=params.tags if "tags" in soft else None,
            location_cities=params.location_cities if "location" in soft else None,
            location_names=params.location_names if "location" in soft else None,
            language=params.language if "language" in soft else None,
            duration_min=params.duration_min if "duration" in soft else None,
            duration_max=params.duration_max if "duration" in soft else None,
            time_windows=None,
        )

    async def _process_chroma_recommendations(
        self,
        chroma_results: list,
        db: Session,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        params: RecommendationQueryParams,
        limit: int,
    ) -> tuple[list[tuple], dict]:
        chroma_id_to_embedding = await self._batch_fetch_embeddings(chroma_results)
        embedding_required = (
            semantic_similarity_enabled or bool(liked_embeddings) or bool(disliked_embeddings)
        )
        recommendations: list[tuple] = []

        for session_id, chroma_similarity, _ in chroma_results:
            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue
            if params.event_id and session.event_id != params.event_id:
                continue

            session_embedding = chroma_id_to_embedding.get(f"session_{session_id}")
            if session_embedding is None:
                if embedding_required:
                    logger.warning("session_embedding_not_found", session_id=session_id)
                session_embedding = self._get_default_embedding()

            soft_compliance = self._compute_soft_filter_compliance(session, params)
            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=params.liked_embedding_weight,
                disliked_embedding_weight=params.disliked_embedding_weight,
                filter_compliance_score=soft_compliance,
                filter_margin_weight=params.filter_margin_weight,
            )
            recommendations.append((session, scores))

        recommendations.sort(key=lambda x: x[1]["overall_score"], reverse=True)

        if params.diversity_weight > 0:
            recommendations = self.diversity_optimizer.diversify_results(
                candidates=recommendations,
                limit=limit,
                diversity_weight=params.diversity_weight,
                embeddings_map=chroma_id_to_embedding,
                session_format=params.session_format,
                tags=params.tags,
                language=params.language,
            )
        else:
            recommendations = recommendations[:limit]
            for _, scores in recommendations:
                scores["diversity_score"] = None

        return recommendations, chroma_id_to_embedding

    async def _recommend_fallback(
        self,
        db: Session,
        params: RecommendationQueryParams,
        limit: int = 10,
    ) -> list[tuple]:
        try:
            soft = self._get_soft_filter_set(params.soft_filters)
            sessions = session_crud.list_with_filters(
                db=db,
                limit=limit + len(params.rejected_ids),
                status=SessionStatus.PUBLISHED,
                event_id=params.event_id,
                session_format=None if "session_format" in soft else params.session_format,
                tags=None if "tags" in soft else params.tags,
                location_cities=None if "location" in soft else params.location_cities,
                location_names=None if "location" in soft else params.location_names,
                language=None if "language" in soft else params.language,
                duration_min=None if "duration" in soft else params.duration_min,
                duration_max=None if "duration" in soft else params.duration_max,
                time_windows=params.time_windows,
            )

            recommendations: list[tuple] = []
            for session in sessions:
                if session.id in params.rejected_ids:
                    continue

                soft_compliance = self._compute_soft_filter_compliance(session, params)
                scores = {
                    "overall_score": soft_compliance if soft_compliance is not None else 1.0,
                    "semantic_similarity": None,
                    "liked_cluster_similarity": None,
                    "disliked_similarity": None,
                    "filter_compliance_score": soft_compliance,
                    "diversity_score": None,
                }
                recommendations.append((session, scores))

            if soft:
                recommendations.sort(key=lambda x: x[1]["overall_score"], reverse=True)
            recommendations = recommendations[:limit]

            logger.info(
                "recommendation_crud_completed",
                rejected_ids_count=len(params.rejected_ids),
                recommendations=len(recommendations),
                limit=limit,
                soft_filters=list(soft),
            )
            return recommendations
        except Exception as e:
            logger.error(
                "recommendation_crud_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmbeddingSearchError(f"CRUD recommendation fallback failed: {e!s}") from e

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        import numpy as np

        v1 = np.asarray(vec1, dtype=np.float32).flatten()
        v2 = np.asarray(vec2, dtype=np.float32).flatten()

        dot_product = float(np.dot(v1, v2))
        norm_v1 = float(np.linalg.norm(v1))
        norm_v2 = float(np.linalg.norm(v2))

        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0

        cosine_sim = dot_product / (norm_v1 * norm_v2)
        return float(min(1.0, max(0.0, (cosine_sim + 1.0) / 2.0)))

    def _compute_liked_similarity(
        self,
        session_embedding: list[float],
        liked_embeddings: dict[int, list[float]],
    ) -> float | None:
        if not liked_embeddings:
            return None

        similarities = [
            self._cosine_similarity(session_embedding, embedding)
            for embedding in liked_embeddings.values()
        ]
        return max(similarities) if similarities else None

    def _compute_disliked_similarity(
        self,
        session_embedding: list[float],
        disliked_embeddings: dict[int, list[float]],
    ) -> float | None:
        if not disliked_embeddings:
            return None

        similarities = [
            self._cosine_similarity(session_embedding, embedding)
            for embedding in disliked_embeddings.values()
        ]
        return max(similarities) if similarities else None

    async def _compute_recommendation_scores(
        self,
        session_embedding: list[float],
        chroma_similarity: float,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_compliance_score: float | None = None,
        filter_margin_weight: float = 0.5,
    ) -> dict[str, float | None]:
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None
        liked_cluster_sim = self._compute_liked_similarity(session_embedding, liked_embeddings)
        disliked_sim = self._compute_disliked_similarity(session_embedding, disliked_embeddings)

        components, component_weights = self.score_engine.build_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            filter_compliance_score=filter_compliance_score,
            weights={
                "liked": liked_embedding_weight,
                "disliked": disliked_embedding_weight,
                "compliance": filter_margin_weight,
            },
        )
        overall_score = self.score_engine.calculate_overall_score(components, component_weights)

        return {
            "overall_score": overall_score,
            "semantic_similarity": semantic_sim,
            "liked_cluster_similarity": liked_cluster_sim,
            "disliked_similarity": disliked_sim,
            "filter_compliance_score": filter_compliance_score,
            "diversity_score": None,
        }
