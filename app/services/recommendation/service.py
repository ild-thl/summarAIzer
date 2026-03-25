"""Recommendation orchestration service.

Keeps recommendation flow and ranking logic isolated from search-only services.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.services.embedding.exceptions import EmbeddingSearchError, InvalidEmbeddingTextError
from app.services.embedding.service import EmbeddingService
from app.services.recommendation.filters import RecommendationFilterEvaluator
from app.services.recommendation.planning import RecommendationPlanner
from app.services.recommendation.scoring import RecommendationScoreEngine

logger = structlog.get_logger()


@dataclass(slots=True)
class RecommendationQueryParams:
    """Shared recommendation query/filter parameters across recommendation modes."""

    query: str | None
    accepted_ids: list[int]
    rejected_ids: list[int]
    event_id: int | None = None
    session_format: list[str] | None = None
    tags: list[str] | None = None
    location: list[str] | None = None
    language: list[str] | None = None
    duration_min: int | None = None
    duration_max: int | None = None
    liked_embedding_weight: float = 0.3
    disliked_embedding_weight: float = 0.2
    filter_mode: str = "hard"
    filter_margin_weight: float = 0.1
    time_windows: list[Any] | None = None


class RecommendationService:
    """Coordinates recommendation execution paths and filter-mode semantic search."""

    def __init__(self, embedding_service: EmbeddingService):
        self.embedding_service = embedding_service
        self.recommendation_planner = RecommendationPlanner()
        self.filter_evaluator = RecommendationFilterEvaluator(self.recommendation_planner)
        self.score_engine = RecommendationScoreEngine()

    @staticmethod
    def _extract_window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
        """Extract (start, end) from TimeWindow objects or plain dicts."""
        if isinstance(window, dict):
            return window.get("start"), window.get("end")
        return getattr(window, "start", None), getattr(window, "end", None)

    @staticmethod
    def _combine_conditions(condition1: dict | None, condition2: dict | None) -> dict | None:
        if not condition1 and not condition2:
            return None
        if not condition1:
            return condition2
        if not condition2:
            return condition1
        return {"$and": [condition1, condition2]}

    def _build_location_condition(self, location: list[str] | None) -> dict | None:
        if not location:
            return None
        location_conditions = [{"location": loc} for loc in location]
        if len(location_conditions) == 1:
            return location_conditions[0]
        return {"$or": location_conditions}

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
    def _build_simple_conditions(
        session_format: list[str] | None,
        language: list[str] | None,
        duration_min: int | None,
        duration_max: int | None,
    ) -> list[dict]:
        conditions = []

        # Handle session_format as OR condition
        if session_format:
            if len(session_format) == 1:
                conditions.append({"session_format": session_format[0]})
            else:
                conditions.append({"$or": [{"session_format": fmt} for fmt in session_format]})

        # Handle language as OR condition
        if language:
            if len(language) == 1:
                conditions.append({"language": language[0]})
            else:
                conditions.append({"$or": [{"language": lang} for lang in language]})

        if duration_min is not None:
            conditions.append({"duration": {"$gte": duration_min}})
        if duration_max is not None:
            conditions.append({"duration": {"$lte": duration_max}})
        return conditions

    def _build_chroma_conditions(
        self,
        session_format: list[str] | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: list[str] | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        time_windows: list[Any] | None = None,
    ) -> dict | None:
        all_conditions = []
        all_conditions.extend(
            self._build_simple_conditions(session_format, language, duration_min, duration_max)
        )

        location_condition = self._build_location_condition(location)
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

    def _optimize_session_plan(
        self,
        recommendations: list[tuple],
        limit: int,
        time_windows: list[Any] | None,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> list[tuple]:
        return self.recommendation_planner.optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
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

        replanned = self._optimize_session_plan(
            recommendations=list(merged_by_session_id.values()),
            limit=limit,
            time_windows=params.time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
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
        query: str | None = None,
        limit: int = 10,
        event_id: int | None = None,
        session_format: list[str] | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: list[str] | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
        filter_mode: str = "hard",
        filter_margin_weight: float = 0.1,
        goal_mode: str = "similarity",
        time_windows: list[Any] | None = None,
        min_break_minutes: int = 0,
        max_gap_minutes: int | None = None,
        plan_candidate_multiplier: int = 3,
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
            location=location,
            language=language,
            duration_min=duration_min,
            duration_max=duration_max,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_mode=filter_mode,
            filter_margin_weight=filter_margin_weight,
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
        query_embedding, semantic_similarity_enabled = await self._determine_query_embedding(
            query=params.query,
            accepted_ids=params.accepted_ids,
            rejected_ids=params.rejected_ids,
        )

        metadata_conditions = self._build_chroma_conditions(
            session_format=params.session_format,
            tags=params.tags,
            location=params.location,
            language=params.language,
            duration_min=params.duration_min,
            duration_max=params.duration_max,
        )
        time_windows_condition = self._build_time_windows_conditions(params.time_windows)
        metadata_conditions = self._combine_conditions(metadata_conditions, time_windows_condition)

        nin_condition = {"session_id": {"$nin": list(seen_ids)}} if seen_ids else None
        chroma_where = self._combine_conditions(nin_condition, metadata_conditions)

        chroma_results_soft = []
        soft_pass_triggered = False

        if params.filter_mode == "soft":
            soft_pass_triggered = True
            chroma_results_hard = []
            try:
                soft_search_limit = candidate_limit * 2
                chroma_results_soft = await self._collect_soft_pass_candidates(
                    query_embedding=query_embedding,
                    soft_search_limit=soft_search_limit,
                    nin_condition=nin_condition,
                    time_windows=params.time_windows,
                )
                logger.info(
                    "recommendation_soft_mode_direct_pass",
                    soft_search_limit=soft_search_limit,
                    soft_results_before_dedup=len(chroma_results_soft),
                )
            except Exception as e:
                logger.warning("recommendation_soft_pass_search_failed", error=str(e))
                chroma_results_soft = []
        else:
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

        preference_embeddings = await self._prefetch_preference_embeddings(
            accepted_ids=params.accepted_ids,
            rejected_ids=params.rejected_ids,
        )
        recommendations = await self._process_chroma_recommendations(
            chroma_results_hard=chroma_results_hard,
            chroma_results_soft=chroma_results_soft,
            db=db,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=preference_embeddings["liked"],
            disliked_embeddings=preference_embeddings["disliked"],
            params=params,
            limit=candidate_limit,
            soft_pass_triggered=soft_pass_triggered,
        )

        return recommendations, {
            "hard_pass_results": len(chroma_results_hard),
            "soft_pass_results": len(chroma_results_soft),
            "soft_pass_triggered": soft_pass_triggered,
        }

    async def _collect_soft_pass_candidates(
        self,
        query_embedding: list[float],
        soft_search_limit: int,
        nin_condition: dict | None,
        time_windows: list[Any] | None,
    ) -> list:
        if not time_windows:
            chroma_where_soft = self._combine_conditions(nin_condition, None)
            if chroma_where_soft:
                return await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=soft_search_limit,
                    where=chroma_where_soft,
                )
            return await self.embedding_service.search_similar_sessions(
                query_embedding,
                limit=soft_search_limit,
            )

        per_window_limit = max(1, soft_search_limit // len(time_windows))
        collected_results = []
        for window in time_windows:
            window_condition = self._build_time_windows_conditions([window])
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

        deduped: dict[int, tuple[int, float, Any]] = {}
        for session_id, similarity, metadata in collected_results:
            current = deduped.get(session_id)
            if current is None or similarity > current[1]:
                deduped[session_id] = (session_id, similarity, metadata)

        deduped_results = list(deduped.values())
        deduped_results.sort(key=lambda item: item[1], reverse=True)
        return deduped_results[:soft_search_limit]

    async def _determine_query_embedding(
        self,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> tuple[list, bool]:
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

        liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
        if not liked_embeddings:
            raise EmbeddingSearchError(f"No embeddings found for liked sessions: {accepted_ids}")

        import numpy as np

        centroid_array = np.mean(list(liked_embeddings.values()), axis=0)
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
        params: RecommendationQueryParams,
        limit: int = 10,
    ) -> list[tuple]:
        try:
            if params.filter_mode == "soft":
                # In soft mode fallback we expand candidates first, then rank by compliance.
                sessions = session_crud.list_with_filters(
                    db=db,
                    limit=(limit * 2) + len(params.rejected_ids),
                    status=SessionStatus.PUBLISHED,
                    event_id=params.event_id,
                    time_windows=params.time_windows,
                )
            else:
                sessions = session_crud.list_with_filters(
                    db=db,
                    limit=limit + len(params.rejected_ids),
                    status=SessionStatus.PUBLISHED,
                    event_id=params.event_id,
                    session_format=params.session_format,
                    tags=params.tags,
                    location=params.location,
                    language=params.language,
                    duration_min=params.duration_min,
                    duration_max=params.duration_max,
                    time_windows=params.time_windows,
                )

            recommendations = []
            if params.filter_mode == "soft":
                soft_candidates = [
                    (session, 0.0, None, "soft")
                    for session in sessions
                    if session.id not in params.rejected_ids
                ]
                ranked_soft = await self._rank_soft_candidates(
                    candidates=soft_candidates,
                    semantic_similarity_enabled=False,
                    liked_embeddings={},
                    disliked_embeddings={},
                    params=replace(
                        params,
                        liked_embedding_weight=0.0,
                        disliked_embedding_weight=0.0,
                    ),
                )
                recommendations = [(session, scores) for session, scores, _ in ranked_soft[:limit]]
            else:
                for session in sessions:
                    if session.id in params.rejected_ids:
                        continue
                    scores = {
                        "overall_score": 1.0,
                        "semantic_similarity": None,
                        "liked_cluster_similarity": None,
                        "disliked_similarity": None,
                        "filter_compliance_score": None,
                    }
                    recommendations.append((session, scores))
                recommendations = recommendations[:limit]

            logger.info(
                "recommendation_crud_completed",
                rejected_ids_count=len(params.rejected_ids),
                recommendations=len(recommendations),
                limit=limit,
                filter_mode=params.filter_mode,
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
        chroma_results_hard: list,
        chroma_results_soft: list,
    ) -> dict:
        # Keep ordering stable but remove duplicates to satisfy Chroma get() constraints.
        all_session_ids = list(
            dict.fromkeys(
                [session_id for session_id, _, _ in chroma_results_hard]
                + [session_id for session_id, _, _ in chroma_results_soft]
            )
        )

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

    async def _process_chroma_recommendations(
        self,
        chroma_results_hard: list,
        chroma_results_soft: list,
        db: Session,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        params: RecommendationQueryParams,
        limit: int,
        soft_pass_triggered: bool = False,
    ) -> list[tuple]:
        chroma_id_to_embedding = await self._batch_fetch_embeddings(
            chroma_results_hard, chroma_results_soft
        )
        hard_pass_session_ids = {session_id for session_id, _, _ in chroma_results_hard}
        recommendations = []

        await self._process_hard_pass_results(
            chroma_results_hard=chroma_results_hard,
            db=db,
            params=params,
            chroma_id_to_embedding=chroma_id_to_embedding,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=liked_embeddings,
            disliked_embeddings=disliked_embeddings,
            limit=limit,
            recommendations=recommendations,
        )

        if soft_pass_triggered and chroma_results_soft:
            await self._process_soft_pass_results(
                chroma_results_soft=chroma_results_soft,
                hard_pass_session_ids=hard_pass_session_ids,
                db=db,
                params=params,
                chroma_id_to_embedding=chroma_id_to_embedding,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                limit=limit,
                recommendations=recommendations,
            )

        recommendations_final = [(session, scores) for session, scores, _ in recommendations]
        recommendations_final.sort(key=lambda x: x[1]["overall_score"], reverse=True)
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

    async def _process_hard_pass_results(
        self,
        chroma_results_hard: list,
        db: Session,
        params: RecommendationQueryParams,
        chroma_id_to_embedding: dict,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        limit: int,
        recommendations: list,
    ) -> None:
        for session_id, chroma_similarity, _ in chroma_results_hard:
            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue
            if params.event_id and session.event_id != params.event_id:
                continue

            session_embedding = chroma_id_to_embedding.get(f"session_{session_id}")
            if session_embedding is None:
                logger.warning("session_embedding_not_found", session_id=session_id)
                session_embedding = self._get_default_embedding()

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=params.liked_embedding_weight,
                disliked_embedding_weight=params.disliked_embedding_weight,
                filter_compliance_score=1.0,
                filter_margin_weight=params.filter_margin_weight,
            )

            recommendations.append((session, scores, "hard"))
            if len(recommendations) >= limit:
                break

    async def _process_soft_pass_results(
        self,
        chroma_results_soft: list,
        hard_pass_session_ids: set,
        db: Session,
        params: RecommendationQueryParams,
        chroma_id_to_embedding: dict,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        limit: int,
        recommendations: list,
    ) -> None:
        soft_candidates: list[tuple[Any, float, list[float] | None, str]] = []
        for session_id, chroma_similarity, _ in chroma_results_soft:
            if session_id in hard_pass_session_ids:
                continue

            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue
            if params.event_id and session.event_id != params.event_id:
                continue
            if any(rec[0].id == session_id for rec in recommendations):
                continue

            session_embedding = chroma_id_to_embedding.get(f"session_{session_id}")
            soft_candidates.append((session, chroma_similarity, session_embedding, "soft"))

        ranked_soft_candidates = await self._rank_soft_candidates(
            candidates=soft_candidates,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=liked_embeddings,
            disliked_embeddings=disliked_embeddings,
            params=params,
        )

        for session, scores, source in ranked_soft_candidates:
            recommendations.append((session, scores, source))
            if len(recommendations) >= limit:
                break

    async def _rank_soft_candidates(
        self,
        candidates: list[tuple[Any, float, list[float] | None, str]],
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        params: RecommendationQueryParams,
    ) -> list[tuple[Any, dict[str, float | None], str]]:
        ranked: list[tuple[Any, dict[str, float | None], str]] = []

        for session, chroma_similarity, session_embedding, source in candidates:
            if session_embedding is None:
                logger.warning("session_embedding_not_found_soft_pass", session_id=session.id)
                session_embedding = self._get_default_embedding()

            filter_compliance_score = self.filter_evaluator.compute_filter_compliance_score(
                session=session,
                session_format=params.session_format,
                tags=params.tags,
                location=params.location,
                language=params.language,
                duration_min=params.duration_min,
                duration_max=params.duration_max,
                time_windows=params.time_windows,
            )

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=params.liked_embedding_weight,
                disliked_embedding_weight=params.disliked_embedding_weight,
                filter_compliance_score=filter_compliance_score,
                filter_margin_weight=params.filter_margin_weight,
            )
            ranked.append((session, scores, source))

        ranked.sort(key=lambda item: item[1]["overall_score"], reverse=True)
        return ranked

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
        return float(max(0.0, (cosine_sim + 1.0) / 2.0))

    def _compute_liked_similarity(
        self,
        session_embedding: list[float],
        liked_embeddings: dict[int, list[float]],
    ) -> float | None:
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
        strategy_weights: dict[str, float] = {
            "liked": liked_embedding_weight,
            "disliked": disliked_embedding_weight,
            "compliance": filter_margin_weight,
        }
        return self.score_engine.build_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            filter_compliance_score=filter_compliance_score,
            weights=strategy_weights,
        )

    @staticmethod
    def _calculate_overall_score(components: list, weights: list) -> float:
        return RecommendationScoreEngine.calculate_overall_score(components, weights)

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
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None
        liked_cluster_sim = self._compute_liked_similarity(session_embedding, liked_embeddings)
        disliked_sim = self._compute_disliked_similarity(session_embedding, disliked_embeddings)

        score_components, score_weights = self._build_score_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_compliance_score=filter_compliance_score,
            filter_margin_weight=filter_margin_weight,
        )
        overall_score = self._calculate_overall_score(score_components, score_weights)

        return {
            "overall_score": round(overall_score, 3),
            "semantic_similarity": round(semantic_sim, 3) if semantic_sim is not None else None,
            "liked_cluster_similarity": (
                round(liked_cluster_sim, 3) if liked_cluster_sim is not None else None
            ),
            "disliked_similarity": round(disliked_sim, 3) if disliked_sim is not None else None,
            "filter_compliance_score": (
                round(filter_compliance_score, 3) if filter_compliance_score is not None else None
            ),
        }
