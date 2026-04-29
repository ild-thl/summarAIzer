"""Recommendation orchestration service.

Keeps recommendation flow and ranking logic isolated from search-only services.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from time import perf_counter
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
from app.services.recommendation.semantic_circuit_breaker import (
    RecommendationSemanticCircuitBreaker,
)

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
    preference_dominance_margin: float = 0.02
    disliked_embedding_weight: float = 0.2
    soft_filters: list[str] | None = None
    filter_margin_weight: float = 0.5
    min_overall_score: float | None = None
    diversity_weight: float = 0.0
    popularity_weight: float = 0.0
    time_windows: list[Any] | None = None
    exclude_parallel_accepted_sessions: bool = False


class RecommendationService:
    """Coordinates recommendation execution paths and filter-mode semantic search."""

    DEFAULT_GAP_FILL_MINUTES: ClassVar[int] = 45
    MAX_GAP_FILL_ATTEMPTS: ClassVar[int] = 10

    SOFT_FILTER_KEYS: ClassVar[set[str]] = {
        "session_format",
        "tags",
        "location",
        "language",
        "duration",
        "time_windows",
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

    def __init__(
        self,
        embedding_service: EmbeddingService,
        semantic_fallback_enabled: bool = True,
        semantic_circuit_breaker: RecommendationSemanticCircuitBreaker | None = None,
    ):
        self.embedding_service = embedding_service
        self.recommendation_planner = RecommendationPlanner()
        self.semantic_fallback_enabled = semantic_fallback_enabled
        self.semantic_circuit_breaker = semantic_circuit_breaker
        self.filter_evaluator = RecommendationFilterEvaluator(self.recommendation_planner)
        self.score_engine = RecommendationScoreEngine()
        self.diversity_optimizer = RecommendationDiversityOptimizer()

    @staticmethod
    def _build_recommendation_debug_payload(
        *,
        hard_pass_results: int = 0,
        soft_pass_results: int = 0,
        soft_pass_triggered: bool = False,
        degraded_to_fallback: bool = False,
        degradation_reason: str | None = None,
    ) -> dict[str, Any]:
        """Build a consistent debug payload for recommendation execution paths."""
        return {
            "hard_pass_results": hard_pass_results,
            "soft_pass_results": soft_pass_results,
            "soft_pass_triggered": soft_pass_triggered,
            "degraded_to_fallback": degraded_to_fallback,
            "degradation_reason": degradation_reason,
        }

    def _semantic_circuit_breaker_enabled(self) -> bool:
        """Return whether semantic circuit breaker protection is active."""
        return self.semantic_fallback_enabled and self.semantic_circuit_breaker is not None

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

    @staticmethod
    def _has_effective_filter_value(filter_key: str, params: RecommendationQueryParams) -> bool:
        if filter_key == "session_format":
            return bool(params.session_format)
        if filter_key == "tags":
            return bool(params.tags)
        if filter_key == "location":
            return bool(params.location_cities or params.location_names)
        if filter_key == "language":
            return bool(params.language)
        if filter_key == "duration":
            return params.duration_min is not None or params.duration_max is not None
        if filter_key == "time_windows":
            return bool(params.time_windows)
        return False

    def _get_effective_soft_filters(self, params: RecommendationQueryParams) -> set[str]:
        requested_soft = self._get_soft_filter_set(params.soft_filters)
        return {
            filter_key
            for filter_key in requested_soft
            if self._has_effective_filter_value(filter_key, params)
        }

    def _apply_score_threshold(
        self,
        recommendations: list[tuple],
        min_overall_score: float | None,
    ) -> list[tuple]:
        if min_overall_score is None:
            return recommendations

        return [
            (session, scores)
            for session, scores in recommendations
            if scores["overall_score"] >= min_overall_score
        ]

    @staticmethod
    def _passes_preference_dominance_check(
        scores: dict[str, Any],
        preference_dominance_margin: float,
    ) -> bool:
        liked_similarity = scores.get("liked_cluster_similarity")
        disliked_similarity = scores.get("disliked_similarity")
        if liked_similarity is None or disliked_similarity is None:
            return True
        return disliked_similarity <= liked_similarity + preference_dominance_margin

    def _apply_preference_dominance_filter(
        self,
        recommendations: list[tuple],
        preference_dominance_margin: float,
    ) -> list[tuple]:
        return [
            (session, scores)
            for session, scores in recommendations
            if self._passes_preference_dominance_check(scores, preference_dominance_margin)
        ]

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
    def _subtract_interval_from_window(
        window: dict[str, datetime],
        occupied_start: datetime,
        occupied_end: datetime,
    ) -> list[dict[str, datetime]]:
        window_start = window["start"]
        window_end = window["end"]
        overlap_start = max(window_start, occupied_start)
        overlap_end = min(window_end, occupied_end)
        if overlap_end <= overlap_start:
            return [window]

        remaining_windows: list[dict[str, datetime]] = []
        if window_start < overlap_start:
            remaining_windows.append({"start": window_start, "end": overlap_start})
        if overlap_end < window_end:
            remaining_windows.append({"start": overlap_end, "end": window_end})
        return remaining_windows

    @staticmethod
    def _merge_time_ranges(
        ranges: list[tuple[datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        if not ranges:
            return []

        sorted_ranges = sorted(ranges, key=lambda value: (value[0], value[1]))
        merged_ranges: list[tuple[datetime, datetime]] = [sorted_ranges[0]]
        for start, end in sorted_ranges[1:]:
            current_start, current_end = merged_ranges[-1]
            if start <= current_end:
                merged_ranges[-1] = (current_start, max(current_end, end))
                continue
            merged_ranges.append((start, end))
        return merged_ranges

    def _subtract_occupied_ranges_from_time_windows(
        self,
        time_windows: list[Any],
        occupied_ranges: list[tuple[datetime, datetime]],
    ) -> list[dict[str, datetime]]:
        remaining_windows: list[dict[str, datetime]] = []
        for window in time_windows:
            window_start, window_end = self._extract_window_bounds(window)
            if window_start is None or window_end is None or window_end <= window_start:
                continue

            window_segments = [{"start": window_start, "end": window_end}]
            for occupied_start, occupied_end in occupied_ranges:
                next_segments: list[dict[str, datetime]] = []
                for segment in window_segments:
                    next_segments.extend(
                        self._subtract_interval_from_window(segment, occupied_start, occupied_end)
                    )
                window_segments = next_segments
                if not window_segments:
                    break
            remaining_windows.extend(window_segments)

        return remaining_windows

    def _apply_accepted_session_time_window_exclusions(
        self,
        db: Session,
        params: RecommendationQueryParams,
    ) -> RecommendationQueryParams:
        if (
            not params.exclude_parallel_accepted_sessions
            or not params.accepted_ids
            or not params.time_windows
        ):
            return params

        accepted_sessions = session_crud.read_many_by_ids(db, params.accepted_ids)
        occupied_ranges = self._merge_time_ranges(
            [
                (session.start_datetime, session.end_datetime)
                for session in accepted_sessions.values()
                if getattr(session, "start_datetime", None) is not None
                and getattr(session, "end_datetime", None) is not None
                and session.end_datetime > session.start_datetime
            ]
        )
        if not occupied_ranges:
            return params

        original_time_window_summary = self._summarize_gap_windows(
            [
                {"start": start, "end": end}
                for start, end in [
                    self._extract_window_bounds(window) for window in params.time_windows or []
                ]
                if start is not None and end is not None and end > start
            ]
        )
        occupied_range_summary = [
            {
                "index": index,
                "start": start,
                "end": end,
                "duration_minutes": round((end - start).total_seconds() / 60, 2),
            }
            for index, (start, end) in enumerate(occupied_ranges)
        ]

        adjusted_time_windows = self._subtract_occupied_ranges_from_time_windows(
            time_windows=params.time_windows,
            occupied_ranges=occupied_ranges,
        )
        adjusted_time_window_summary = self._summarize_gap_windows(adjusted_time_windows)

        logger.debug(
            "recommendation_time_windows_excluding_accepted_sessions",
            accepted_ids=params.accepted_ids,
            accepted_sessions_found=len(accepted_sessions),
            original_time_windows=original_time_window_summary,
            occupied_ranges=occupied_range_summary,
            adjusted_time_windows=adjusted_time_window_summary,
        )
        return replace(params, time_windows=adjusted_time_windows)

    @classmethod
    def _get_gap_fill_min_minutes(cls, max_gap_minutes: int | None) -> int:
        if max_gap_minutes is not None:
            return max_gap_minutes
        return cls.DEFAULT_GAP_FILL_MINUTES

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
        min_gap_delta = self._build_min_gap_delta(self._get_gap_fill_min_minutes(max_gap_minutes))
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

    @staticmethod
    def _gap_window_duration_minutes(window: dict[str, datetime]) -> float:
        start = window.get("start")
        end = window.get("end")
        if start is None or end is None or end <= start:
            return 0.0
        return round((end - start).total_seconds() / 60, 2)

    def _summarize_gap_windows(
        self, gap_windows: list[dict[str, datetime]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "start": window["start"],
                "end": window["end"],
                "duration_minutes": self._gap_window_duration_minutes(window),
            }
            for index, window in enumerate(gap_windows)
        ]

    @staticmethod
    def _session_fits_gap_window(session: Any, window: dict[str, datetime]) -> bool:
        start = window.get("start")
        end = window.get("end")
        session_start = getattr(session, "start_datetime", None)
        session_end = getattr(session, "end_datetime", None)
        if None in (start, end, session_start, session_end):
            return False
        return session_start >= start and session_end <= end

    def _summarize_gap_fill_candidates(
        self,
        gap_windows: list[dict[str, datetime]],
        candidates: list[tuple],
    ) -> list[dict[str, Any]]:
        summaries = self._summarize_gap_windows(gap_windows)
        for summary, window in zip(summaries, gap_windows, strict=False):
            matching_candidates = [
                session.id
                for session, _ in candidates
                if self._session_fits_gap_window(session, window)
            ]
            summary["candidate_count"] = len(matching_candidates)
            summary["candidate_session_ids"] = matching_candidates
        return summaries

    @staticmethod
    def _gap_window_key(window: dict[str, datetime]) -> tuple[datetime | None, datetime | None]:
        return window.get("start"), window.get("end")

    def _select_gap_fill_windows(
        self,
        gap_windows: list[dict[str, datetime]],
        attempted_gap_keys: set[tuple[datetime | None, datetime | None]],
    ) -> list[dict[str, datetime]]:
        ranked_gap_windows = sorted(
            gap_windows,
            key=lambda window: (
                -self._gap_window_duration_minutes(window),
                window.get("start") or datetime.min,
                window.get("end") or datetime.min,
            ),
        )
        return [
            window
            for window in ranked_gap_windows
            if self._gap_window_key(window) not in attempted_gap_keys
        ]

    @staticmethod
    def _supports_semantic_gap_fill(params: RecommendationQueryParams) -> bool:
        return bool(params.query or params.accepted_ids)

    def _select_sessions_for_gap(
        self,
        recommendations: list[tuple],
        gap_window: dict[str, datetime],
        min_break_minutes: int,
        max_gap_minutes: int | None,
        limit: int,
    ) -> list[tuple]:
        return self._optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=[gap_window],
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
        )

    async def _collect_semantic_gap_fill_recommendations(
        self,
        db: Session,
        params: RecommendationQueryParams,
        planned: list[tuple],
        gap_window: dict[str, datetime],
        candidate_limit: int,
        query_embeddings: list[list[float]],
        semantic_similarity_enabled: bool,
    ) -> list[tuple]:
        planned_ids = [session.id for session, _ in planned]
        seen_ids = set(params.accepted_ids + params.rejected_ids + planned_ids)
        gap_params = replace(params, time_windows=[gap_window])
        recommendations, _ = await self._recommend_with_semantic_search(
            db=db,
            params=gap_params,
            seen_ids=seen_ids,
            candidate_limit=candidate_limit,
            query_embeddings=query_embeddings,
            semantic_similarity_enabled=semantic_similarity_enabled,
        )
        return recommendations

    async def _try_semantic_gap_fill_rerank(
        self,
        db: Session,
        params: RecommendationQueryParams,
        replanned: list[tuple],
        target_gap: dict[str, datetime],
        gap_candidate_limit: int,
        cached_query_embeddings: list[list[float]] | None,
        cached_semantic_similarity_enabled: bool | None,
        remaining_slots: int,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> tuple[
        list[tuple] | None,
        int,
        str,
        float,
        list[list[float]] | None,
        bool | None,
        float,
    ]:
        """Try semantic gap-fill reranking and degrade gracefully on transient failures."""
        circuit_is_open = False
        circuit_open_until: datetime | None = None
        if self._semantic_circuit_breaker_enabled():
            circuit_is_open, circuit_open_until, _ = await self.semantic_circuit_breaker.is_open()

        if circuit_is_open:
            logger.warning(
                "recommendation_gap_fill_semantic_bypassed_circuit_open",
                gap_window=self._summarize_gap_windows([target_gap])[0],
                candidate_limit=gap_candidate_limit,
                circuit_open_until=circuit_open_until,
            )
            return (
                None,
                0,
                "fallback_semantic_circuit_open",
                0.0,
                cached_query_embeddings,
                cached_semantic_similarity_enabled,
                0.0,
            )

        query_embedding_ms = 0.0
        if cached_query_embeddings is None or cached_semantic_similarity_enabled is None:
            query_embedding_start = perf_counter()
            (
                cached_query_embeddings,
                cached_semantic_similarity_enabled,
            ) = await self._determine_query_embeddings(
                query=params.query,
                accepted_ids=params.accepted_ids,
                rejected_ids=params.rejected_ids,
            )
            query_embedding_ms = round((perf_counter() - query_embedding_start) * 1000, 2)

        try:
            semantic_gap_fill_start = perf_counter()
            semantic_gap_fill_candidates = await self._collect_semantic_gap_fill_recommendations(
                db=db,
                params=params,
                planned=replanned,
                gap_window=target_gap,
                candidate_limit=gap_candidate_limit,
                query_embeddings=cached_query_embeddings,
                semantic_similarity_enabled=cached_semantic_similarity_enabled,
            )
            semantic_gap_fill_ms = round((perf_counter() - semantic_gap_fill_start) * 1000, 2)
            if not semantic_gap_fill_candidates:
                return (
                    [],
                    0,
                    "fallback_plus_semantic_rerank",
                    semantic_gap_fill_ms,
                    cached_query_embeddings,
                    cached_semantic_similarity_enabled,
                    query_embedding_ms,
                )

            selected_for_gap = self._select_sessions_for_gap(
                recommendations=semantic_gap_fill_candidates,
                gap_window=target_gap,
                min_break_minutes=min_break_minutes,
                max_gap_minutes=max_gap_minutes,
                limit=remaining_slots,
            )
            return (
                selected_for_gap,
                len(semantic_gap_fill_candidates),
                "fallback_plus_semantic_rerank",
                semantic_gap_fill_ms,
                cached_query_embeddings,
                cached_semantic_similarity_enabled,
                query_embedding_ms,
            )
        except InvalidEmbeddingTextError:
            raise
        except Exception as exc:
            failure_count = 0
            circuit_open_until = None
            if self._semantic_circuit_breaker_enabled():
                failure_count, circuit_open_until = (
                    await self.semantic_circuit_breaker.record_failure(type(exc).__name__)
                )
            logger.warning(
                "recommendation_gap_fill_semantic_rerank_degraded",
                error=str(exc),
                error_type=type(exc).__name__,
                gap_window=self._summarize_gap_windows([target_gap])[0],
                candidate_limit=gap_candidate_limit,
                consecutive_failures=failure_count,
                circuit_open_until=circuit_open_until,
            )
            return (
                None,
                0,
                "fallback_semantic_rerank_degraded",
                0.0,
                cached_query_embeddings,
                cached_semantic_similarity_enabled,
                query_embedding_ms,
            )

    async def _collect_gap_fill_recommendations(
        self,
        db: Session,
        params: RecommendationQueryParams,
        planned: list[tuple],
        gap_window: dict[str, datetime],
        candidate_limit: int,
    ) -> tuple[list[tuple], str]:
        planned_ids = [session.id for session, _ in planned]
        gap_params = replace(params, time_windows=[gap_window])
        fallback_params = replace(
            gap_params,
            accepted_ids=list(dict.fromkeys(params.accepted_ids + planned_ids)),
            rejected_ids=list(dict.fromkeys(params.rejected_ids)),
        )
        recommendations = await self._recommend_fallback(
            db=db,
            params=fallback_params,
            limit=candidate_limit,
        )
        return recommendations, "fallback"

    @staticmethod
    def _merge_recommendation_lists(
        existing_recommendations: list[tuple],
        new_recommendations: list[tuple],
    ) -> list[tuple]:
        merged_by_session_id: dict[int, tuple] = {
            session.id: (session, scores) for session, scores in existing_recommendations
        }
        for session, scores in new_recommendations:
            current = merged_by_session_id.get(session.id)
            if current is None or scores["overall_score"] > current[1]["overall_score"]:
                merged_by_session_id[session.id] = (session, scores)
        return sorted(
            merged_by_session_id.values(),
            key=lambda item: (item[0].start_datetime, item[0].id),
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
                popularity_sort=True,
            )
            return recommendations, self._build_recommendation_debug_payload()

        circuit_is_open = False
        circuit_open_until: datetime | None = None
        consecutive_failures = 0
        if self._semantic_circuit_breaker_enabled():
            (
                circuit_is_open,
                circuit_open_until,
                consecutive_failures,
            ) = await self.semantic_circuit_breaker.is_open()

        if circuit_is_open:
            logger.warning(
                "recommendation_semantic_bypassed_circuit_open",
                candidate_limit=candidate_limit,
                circuit_open_until=circuit_open_until,
                consecutive_failures=consecutive_failures,
            )
            recommendations = await self._recommend_fallback(
                db=db,
                params=params,
                limit=candidate_limit,
            )
            return recommendations, self._build_recommendation_debug_payload(
                degraded_to_fallback=True,
                degradation_reason="semantic_circuit_open",
            )

        try:
            recommendations, debug = await self._recommend_with_semantic_search(
                db=db,
                params=params,
                seen_ids=seen_ids,
                candidate_limit=candidate_limit,
            )
            if self._semantic_circuit_breaker_enabled():
                await self.semantic_circuit_breaker.record_success()
            return recommendations, debug
        except InvalidEmbeddingTextError:
            raise
        except Exception as exc:
            failure_count = 0
            circuit_open_until = None
            if self._semantic_circuit_breaker_enabled():
                failure_count, circuit_open_until = (
                    await self.semantic_circuit_breaker.record_failure(type(exc).__name__)
                )
            if not self.semantic_fallback_enabled:
                raise

            logger.warning(
                "recommendation_semantic_degraded_to_fallback",
                error=str(exc),
                error_type=type(exc).__name__,
                candidate_limit=candidate_limit,
                accepted_ids_count=len(params.accepted_ids),
                rejected_ids_count=len(params.rejected_ids),
                query_count=len(self._normalize_query_list(params.query)),
                consecutive_failures=failure_count,
                circuit_open_until=circuit_open_until,
            )
            recommendations = await self._recommend_fallback(
                db=db,
                params=params,
                limit=candidate_limit,
            )
            return recommendations, self._build_recommendation_debug_payload(
                degraded_to_fallback=True,
                degradation_reason=type(exc).__name__,
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
        benchmark_start = perf_counter()
        candidate_limit = limit * plan_candidate_multiplier
        base_collect_start = perf_counter()
        recommendations, search_debug = await self._collect_base_recommendations(
            db=db,
            params=params,
            seen_ids=seen_ids,
            candidate_limit=candidate_limit,
        )
        base_collect_ms = round((perf_counter() - base_collect_start) * 1000, 2)

        # First pass: optimize without diversity (pure relevance ranking)
        initial_plan_start = perf_counter()
        planned = self._optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=params.time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
        )
        initial_plan_ms = round((perf_counter() - initial_plan_start) * 1000, 2)

        gap_window_start = perf_counter()
        gap_fill_windows = self._derive_gap_fill_windows(
            planned=planned,
            time_windows=params.time_windows,
            max_gap_minutes=max_gap_minutes,
        )
        gap_window_ms = round((perf_counter() - gap_window_start) * 1000, 2)
        gap_fill_window_summary = self._summarize_gap_windows(gap_fill_windows)
        gap_fill_min_minutes = self._get_gap_fill_min_minutes(max_gap_minutes)
        if not gap_fill_windows:
            logger.debug(
                "recommendation_plan_gap_fill_skipped_no_oversized_gaps",
                planned_size=len(planned),
                requested_limit=limit,
                max_gap_minutes=max_gap_minutes,
                min_gap_fill_minutes=gap_fill_min_minutes,
                gap_count=0,
                gap_windows=[],
            )
            logger.debug(
                "recommendation_benchmark_plan_mode",
                candidate_limit=candidate_limit,
                requested_limit=limit,
                base_collect_ms=base_collect_ms,
                initial_plan_ms=initial_plan_ms,
                gap_window_ms=gap_window_ms,
                total_ms=round((perf_counter() - benchmark_start) * 1000, 2),
            )
            return planned, search_debug

        logger.debug(
            "recommendation_plan_gap_fill_windows_detected",
            planned_size=len(planned),
            requested_limit=limit,
            min_gap_fill_minutes=gap_fill_min_minutes,
            gap_count=len(gap_fill_windows),
            total_gap_minutes=round(
                sum(summary["duration_minutes"] for summary in gap_fill_window_summary),
                2,
            ),
            gap_windows=gap_fill_window_summary,
        )

        gap_fill_fetch_ms_total = 0.0
        attempted_gap_keys: set[tuple[datetime | None, datetime | None]] = set()
        gap_fill_attempt_summaries: list[dict[str, Any]] = []
        gap_fill_candidates_total = 0
        gap_filled_sessions_total = 0
        replanned = list(planned)
        candidate_gap_windows = self._select_gap_fill_windows(
            gap_windows=gap_fill_windows,
            attempted_gap_keys=attempted_gap_keys,
        )[: self.MAX_GAP_FILL_ATTEMPTS]
        cached_query_embeddings: list[list[float]] | None = None
        cached_semantic_similarity_enabled: bool | None = None
        cached_query_embedding_ms = 0.0
        semantic_rerank_count = 0

        for target_gap in candidate_gap_windows:
            if len(replanned) >= limit:
                break

            attempted_gap_keys.add(self._gap_window_key(target_gap))
            remaining_slots = limit - len(replanned)
            gap_candidate_limit = max(remaining_slots * plan_candidate_multiplier, remaining_slots)

            gap_fill_start = perf_counter()
            gap_fill_candidates, retrieval_mode = await self._collect_gap_fill_recommendations(
                db=db,
                params=params,
                planned=replanned,
                gap_window=target_gap,
                candidate_limit=gap_candidate_limit,
            )
            gap_fill_fetch_ms = round((perf_counter() - gap_fill_start) * 1000, 2)
            gap_fill_fetch_ms_total += gap_fill_fetch_ms

            gap_fill_candidates_total += len(gap_fill_candidates)

            fallback_feasible_for_gap = self._select_sessions_for_gap(
                recommendations=gap_fill_candidates,
                gap_window=target_gap,
                min_break_minutes=min_break_minutes,
                max_gap_minutes=max_gap_minutes,
                limit=len(gap_fill_candidates),
            )
            selected_for_gap = self._select_sessions_for_gap(
                recommendations=gap_fill_candidates,
                gap_window=target_gap,
                min_break_minutes=min_break_minutes,
                max_gap_minutes=max_gap_minutes,
                limit=remaining_slots,
            )
            semantic_rerank_triggered = False
            semantic_candidate_count = 0

            if (
                self._supports_semantic_gap_fill(params)
                and len(gap_fill_candidates) > 3
                and len(fallback_feasible_for_gap) > 0
                and len(fallback_feasible_for_gap) < len(gap_fill_candidates)
            ):
                semantic_rerank_triggered = True
                semantic_rerank_count += 1
                (
                    semantic_selected_for_gap,
                    semantic_candidate_count,
                    retrieval_mode,
                    semantic_gap_fill_ms,
                    cached_query_embeddings,
                    cached_semantic_similarity_enabled,
                    semantic_query_embedding_ms,
                ) = await self._try_semantic_gap_fill_rerank(
                    db=db,
                    params=params,
                    replanned=replanned,
                    target_gap=target_gap,
                    gap_candidate_limit=gap_candidate_limit,
                    cached_query_embeddings=cached_query_embeddings,
                    cached_semantic_similarity_enabled=cached_semantic_similarity_enabled,
                    remaining_slots=remaining_slots,
                    min_break_minutes=min_break_minutes,
                    max_gap_minutes=max_gap_minutes,
                )
                cached_query_embedding_ms = max(
                    cached_query_embedding_ms,
                    semantic_query_embedding_ms,
                )
                gap_fill_fetch_ms_total += semantic_gap_fill_ms
                if semantic_selected_for_gap is not None:
                    selected_for_gap = semantic_selected_for_gap

            gap_filled_sessions_total += len(selected_for_gap)
            if selected_for_gap:
                replanned = self._merge_recommendation_lists(replanned, selected_for_gap)

            gap_summary = {
                **self._summarize_gap_windows([target_gap])[0],
                "candidate_limit": gap_candidate_limit,
                "candidate_count": len(gap_fill_candidates),
                "candidate_session_ids": [session.id for session, _ in gap_fill_candidates],
                "feasible_candidate_count": len(fallback_feasible_for_gap),
                "selected_count": len(selected_for_gap),
                "selected_session_ids": [session.id for session, _ in selected_for_gap],
                "semantic_rerank_triggered": semantic_rerank_triggered,
                "semantic_candidate_count": semantic_candidate_count,
                "retrieval_mode": retrieval_mode,
                "fetch_ms": gap_fill_fetch_ms,
            }
            gap_fill_attempt_summaries.append(gap_summary)
            logger.debug("recommendation_plan_gap_fill_attempt", **gap_summary)

        if self._supports_semantic_gap_fill(params):
            logger.debug(
                "recommendation_plan_gap_fill_semantic_rerank",
                attempted_gap_count=len(candidate_gap_windows),
                semantic_rerank_count=semantic_rerank_count,
                query_embedding_ms=cached_query_embedding_ms,
                gap_fill_fetch_ms=round(gap_fill_fetch_ms_total, 2),
            )

        logger.debug(
            "recommendation_plan_gap_fill_candidate_coverage",
            gap_count=len(gap_fill_windows),
            attempted_gap_count=len(gap_fill_attempt_summaries),
            gap_fill_candidates=gap_fill_candidates_total,
            gap_fill_selected_sessions=gap_filled_sessions_total,
            covered_gap_count=sum(
                1 for summary in gap_fill_attempt_summaries if summary["candidate_count"] > 0
            ),
            uncovered_gap_count=sum(
                1 for summary in gap_fill_attempt_summaries if summary["candidate_count"] == 0
            ),
            gap_windows=gap_fill_attempt_summaries,
        )

        if gap_fill_candidates_total == 0:
            logger.debug(
                "recommendation_plan_gap_fill_remaining_gaps",
                remaining_gap_count=len(gap_fill_windows),
                remaining_total_gap_minutes=round(
                    sum(summary["duration_minutes"] for summary in gap_fill_window_summary),
                    2,
                ),
                remaining_gap_windows=gap_fill_window_summary,
            )
            logger.debug(
                "recommendation_benchmark_plan_mode",
                candidate_limit=candidate_limit,
                requested_limit=limit,
                base_collect_ms=base_collect_ms,
                initial_plan_ms=initial_plan_ms,
                gap_window_ms=gap_window_ms,
                gap_fill_fetch_ms=round(gap_fill_fetch_ms_total, 2),
                total_ms=round((perf_counter() - benchmark_start) * 1000, 2),
            )
            return planned, search_debug

        diversity_ms = 0.0
        replan_ms = 0.0
        remaining_gap_windows = self._derive_gap_fill_windows(
            planned=replanned,
            time_windows=params.time_windows,
            max_gap_minutes=max_gap_minutes,
        )
        remaining_gap_summary = self._summarize_gap_windows(remaining_gap_windows)

        logger.debug(
            "recommendation_plan_gap_fill_remaining_gaps",
            initial_gap_count=len(gap_fill_windows),
            initial_total_gap_minutes=round(
                sum(summary["duration_minutes"] for summary in gap_fill_window_summary),
                2,
            ),
            remaining_gap_count=len(remaining_gap_windows),
            remaining_total_gap_minutes=round(
                sum(summary["duration_minutes"] for summary in remaining_gap_summary),
                2,
            ),
            remaining_gap_windows=remaining_gap_summary,
        )

        logger.debug(
            "recommendation_plan_gap_fill_completed",
            initial_plan_size=len(planned),
            initial_gap_count=len(gap_fill_windows),
            attempted_gap_count=len(gap_fill_attempt_summaries),
            gap_fill_candidates=gap_fill_candidates_total,
            gap_fill_selected_sessions=gap_filled_sessions_total,
            final_plan_size=len(replanned),
            remaining_gap_count=len(remaining_gap_windows),
            requested_limit=limit,
        )
        logger.debug(
            "recommendation_benchmark_plan_mode",
            candidate_limit=candidate_limit,
            requested_limit=limit,
            base_collect_ms=base_collect_ms,
            initial_plan_ms=initial_plan_ms,
            gap_window_ms=gap_window_ms,
            gap_fill_fetch_ms=round(gap_fill_fetch_ms_total, 2),
            diversity_ms=diversity_ms,
            replan_ms=replan_ms,
            total_ms=round((perf_counter() - benchmark_start) * 1000, 2),
        )
        return replanned, search_debug

    async def _recommend_default_mode(
        self,
        db: Session,
        params: RecommendationQueryParams,
        seen_ids: set[int],
        limit: int,
        plan_candidate_multiplier: int,
    ) -> tuple[list[tuple], dict[str, Any]]:
        candidate_limit = limit * plan_candidate_multiplier
        recommendations, search_debug = await self._collect_base_recommendations(
            db=db,
            params=params,
            seen_ids=seen_ids,
            candidate_limit=candidate_limit,
        )
        return recommendations[:limit], search_debug

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
        preference_dominance_margin: float = 0.02,
        disliked_embedding_weight: float = 0.2,
        soft_filters: list[str] | None = None,
        filter_margin_weight: float = 0.5,
        min_overall_score: float | None = None,
        diversity_weight: float = 0.3,
        popularity_weight: float = 0.0,
        goal_mode: str = "similarity",
        time_windows: list[Any] | None = None,
        min_break_minutes: int = 0,
        max_gap_minutes: int | None = None,
        plan_candidate_multiplier: int = 2,
        exclude_parallel_accepted_sessions: bool = False,
    ) -> list[tuple]:
        request_start = perf_counter()
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
            preference_dominance_margin=preference_dominance_margin,
            soft_filters=soft_filters,
            filter_margin_weight=filter_margin_weight,
            min_overall_score=min_overall_score,
            diversity_weight=diversity_weight,
            popularity_weight=popularity_weight,
            time_windows=time_windows,
            exclude_parallel_accepted_sessions=exclude_parallel_accepted_sessions,
        )
        params = self._apply_accepted_session_time_window_exclusions(db=db, params=params)
        requested_soft_filters = params.soft_filters
        effective_soft = self._get_effective_soft_filters(params)
        params = replace(params, soft_filters=sorted(effective_soft))
        seen_ids = set(params.accepted_ids + params.rejected_ids)

        logger.info(
            "recommendation_request",
            goal_mode=goal_mode,
            limit=limit,
            plan_candidate_multiplier=plan_candidate_multiplier,
            event_id=params.event_id,
            query=params.query,
            accepted_ids_count=len(params.accepted_ids),
            rejected_ids_count=len(params.rejected_ids),
            session_format=params.session_format,
            tags=params.tags,
            location_cities=params.location_cities,
            location_names=params.location_names,
            language=params.language,
            duration_min=params.duration_min,
            duration_max=params.duration_max,
            time_windows_count=len(params.time_windows or []),
            requested_soft_filters=requested_soft_filters,
            effective_soft_filters=sorted(effective_soft),
            preference_dominance_margin=params.preference_dominance_margin,
            min_overall_score=params.min_overall_score,
            diversity_weight=params.diversity_weight,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
            exclude_parallel_accepted_sessions=params.exclude_parallel_accepted_sessions,
        )

        if time_windows is not None and not params.time_windows:
            logger.info(
                "recommendation_completed",
                hard_pass_results=0,
                soft_pass_results=0,
                final_recommendations=0,
                limit=limit,
                soft_pass_triggered=False,
                goal_mode=goal_mode,
                elapsed_ms=round((perf_counter() - request_start) * 1000, 2),
            )
            return []

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
                    plan_candidate_multiplier=plan_candidate_multiplier,
                )

            logger.info(
                "recommendation_completed",
                hard_pass_results=search_debug["hard_pass_results"],
                soft_pass_results=search_debug["soft_pass_results"],
                final_recommendations=len(recommendations),
                limit=limit,
                soft_pass_triggered=search_debug["soft_pass_triggered"],
                degraded_to_fallback=search_debug.get("degraded_to_fallback", False),
                degradation_reason=search_debug.get("degradation_reason"),
                goal_mode=goal_mode,
                elapsed_ms=round((perf_counter() - request_start) * 1000, 2),
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
        query_embeddings: list[list[float]] | None = None,
        semantic_similarity_enabled: bool | None = None,
    ) -> tuple[list[tuple], dict[str, Any]]:
        benchmark_start = perf_counter()
        query_embedding_ms = 0.0
        if query_embeddings is None or semantic_similarity_enabled is None:
            query_embedding_start = perf_counter()
            query_embeddings, semantic_similarity_enabled = await self._determine_query_embeddings(
                query=params.query,
                accepted_ids=params.accepted_ids,
                rejected_ids=params.rejected_ids,
            )
            query_embedding_ms = round((perf_counter() - query_embedding_start) * 1000, 2)

        soft = self._get_effective_soft_filters(params)
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
            time_windows=None if "time_windows" in soft else params.time_windows,
        )

        chroma_results: list[tuple] = []
        chroma_search_start = perf_counter()
        for query_embedding in query_embeddings:
            try:
                results = await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=search_limit,
                    where=where_condition,
                )
                chroma_results.extend(results)
            except Exception as e:
                logger.error("recommendation_chroma_search_failed", error=str(e))
                raise EmbeddingSearchError(f"Semantic search failed: {e!s}") from e
        chroma_search_ms = round((perf_counter() - chroma_search_start) * 1000, 2)

        if len(query_embeddings) > 1:
            chroma_results = self._dedupe_chroma_results_by_similarity(chroma_results)[
                :search_limit
            ]

        if has_soft:
            logger.debug(
                "recommendation_soft_filters_active",
                soft_filters=list(soft),
                search_limit=search_limit,
                chroma_results_count=len(chroma_results),
                query_count=len(query_embeddings),
            )

        preference_prefetch_start = perf_counter()
        preference_embeddings = await self._prefetch_preference_embeddings(
            accepted_ids=params.accepted_ids,
            rejected_ids=params.rejected_ids,
        )
        preference_prefetch_ms = round((perf_counter() - preference_prefetch_start) * 1000, 2)

        processing_start = perf_counter()
        recommendations = await self._process_chroma_recommendations(
            chroma_results=chroma_results,
            db=db,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=preference_embeddings["liked"],
            disliked_embeddings=preference_embeddings["disliked"],
            params=params,
            limit=candidate_limit,
        )
        processing_ms = round((perf_counter() - processing_start) * 1000, 2)

        logger.debug(
            "recommendation_benchmark_semantic_search",
            query_embedding_ms=query_embedding_ms,
            chroma_search_ms=chroma_search_ms,
            preference_prefetch_ms=preference_prefetch_ms,
            recommendation_processing_ms=processing_ms,
            total_ms=round((perf_counter() - benchmark_start) * 1000, 2),
            candidate_limit=candidate_limit,
            search_limit=search_limit,
            query_count=len(query_embeddings),
            result_count=len(chroma_results),
            soft_filters=list(soft),
        )

        return recommendations, self._build_recommendation_debug_payload(
            hard_pass_results=0 if has_soft else len(chroma_results),
            soft_pass_results=len(chroma_results) if has_soft else 0,
            soft_pass_triggered=has_soft,
        )

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
            return centroid, True

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
                if not isinstance(liked_embeddings, dict):
                    logger.warning(
                        "liked_embeddings_prefetch_invalid_result",
                        result_type=type(liked_embeddings).__name__,
                        accepted_ids_count=len(accepted_ids),
                    )
                    liked_embeddings = {}
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
                if not isinstance(disliked_embeddings, dict):
                    logger.warning(
                        "disliked_embeddings_prefetch_invalid_result",
                        result_type=type(disliked_embeddings).__name__,
                        rejected_ids_count=len(rejected_ids),
                    )
                    disliked_embeddings = {}
            except Exception as e:
                logger.warning(
                    "disliked_embeddings_prefetch_failed",
                    error=str(e),
                    rejected_ids_count=len(rejected_ids),
                )

        return {"liked": liked_embeddings, "disliked": disliked_embeddings}

    def _get_default_embedding(self) -> list[float]:
        return [0.0] * self.embedding_service.embedding_dimension

    async def _fetch_embeddings_for_session_ids(
        self,
        session_ids: list[int],
    ) -> dict[str, list[float]]:
        chroma_id_to_embedding: dict[str, list[float]] = {}
        if not session_ids:
            return chroma_id_to_embedding

        try:
            embeddings_dict = await self.embedding_service.get_session_embeddings(session_ids)
            if not isinstance(embeddings_dict, dict):
                logger.warning(
                    "batch_embedding_retrieval_invalid_result",
                    result_type=type(embeddings_dict).__name__,
                    sessions_count=len(session_ids),
                )
                return {}
            for session_id, embedding in embeddings_dict.items():
                chroma_id_to_embedding[f"session_{session_id}"] = embedding
        except Exception as e:
            logger.warning(
                "batch_embedding_retrieval_failed",
                error=str(e),
                sessions_count=len(session_ids),
            )
        return chroma_id_to_embedding

    async def _batch_fetch_embeddings(
        self,
        chroma_results_hard: list[tuple] | None,
        chroma_results_soft: list[tuple] | None = None,
    ) -> dict[str, list[float]]:
        combined_results = [
            *(chroma_results_hard or []),
            *(chroma_results_soft or []),
        ]
        all_session_ids = list(dict.fromkeys(session_id for session_id, _, _ in combined_results))
        return await self._fetch_embeddings_for_session_ids(all_session_ids)

    def _compute_soft_filter_compliance(
        self,
        session: Any,
        params: RecommendationQueryParams,
    ) -> float | None:
        """Compute compliance only for soft_filter attributes. Returns None if no soft filters active."""
        soft = self._get_effective_soft_filters(params)
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
            time_windows=params.time_windows if "time_windows" in soft else None,
        )

    @staticmethod
    def _load_sessions_for_chroma_results(db: Session, chroma_results: list) -> dict[int, Any]:
        session_ids = [session_id for session_id, _, _ in chroma_results]
        if not session_ids:
            return {}

        bulk_loader = getattr(session_crud, "read_many_by_ids", None)
        if not callable(bulk_loader):
            return {}

        try:
            bulk_sessions = bulk_loader(db, session_ids)
            if isinstance(bulk_sessions, dict):
                return bulk_sessions
        except Exception as e:
            logger.warning("recommendation_bulk_session_load_failed", error=str(e))
        return {}

    def _finalize_recommendations(
        self,
        recommendations: list[tuple],
        limit: int,
        params: RecommendationQueryParams,
    ) -> list[tuple]:
        recommendations = self._apply_preference_dominance_filter(
            recommendations,
            params.preference_dominance_margin,
        )
        recommendations = self._apply_score_threshold(recommendations, params.min_overall_score)
        recommendations.sort(key=lambda x: x[1]["overall_score"], reverse=True)

        if params.diversity_weight > 0:
            return self.diversity_optimizer.diversify_results(
                candidates=recommendations,
                limit=limit,
                diversity_weight=params.diversity_weight,
                session_format=params.session_format,
                tags=params.tags,
                language=params.language,
            )

        top = recommendations[:limit]
        for _, scores in top:
            scores["diversity_score"] = None
        return top

    async def _process_chroma_recommendations(
        self,
        chroma_results: list,
        db: Session,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        params: RecommendationQueryParams,
        limit: int,
    ) -> list[tuple]:
        benchmark_start = perf_counter()
        chroma_id_to_embedding = await self._batch_fetch_embeddings(
            chroma_results_hard=chroma_results
        )
        embedding_fetch_ms = round((perf_counter() - benchmark_start) * 1000, 2)
        embedding_required = (
            semantic_similarity_enabled or bool(liked_embeddings) or bool(disliked_embeddings)
        )
        recommendations: list[tuple] = []

        bulk_load_start = perf_counter()
        sessions_by_id = self._load_sessions_for_chroma_results(
            db=db, chroma_results=chroma_results
        )
        bulk_session_load_ms = round((perf_counter() - bulk_load_start) * 1000, 2)

        from app.crud.session_popularity import session_popularity_crud

        chroma_session_ids = [sid for sid, _, _ in chroma_results]
        popularity_map = session_popularity_crud.get_popularity_map(
            db=db, session_ids=chroma_session_ids, event_id=params.event_id
        )
        event_max_acceptance = (
            session_popularity_crud.get_event_max_acceptance(db=db, event_id=params.event_id)
            if params.popularity_weight > 0
            else 0
        )

        scoring_start = perf_counter()
        for session_id, chroma_similarity, _ in chroma_results:
            session = sessions_by_id.get(session_id)
            if session is None:
                session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue
            if params.event_id and session.event_id != params.event_id:
                continue

            session_embedding = chroma_id_to_embedding.get(f"session_{session_id}")
            if session_embedding is None:
                if embedding_required:
                    logger.debug("session_embedding_not_found", session_id=session_id)
                session_embedding = self._get_default_embedding()

            pop_data = popularity_map.get(session_id, {})
            popularity_score = (
                session_popularity_crud.compute_popularity_score(
                    pop_data.get("acceptance_count", 0), event_max_acceptance
                )
                if params.popularity_weight > 0
                else None
            )

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
                popularity_score=popularity_score,
                popularity_weight=params.popularity_weight,
            )
            recommendations.append((session, scores))
        scoring_ms = round((perf_counter() - scoring_start) * 1000, 2)

        post_process_start = perf_counter()
        recommendations = self._finalize_recommendations(
            recommendations=recommendations,
            limit=limit,
            params=params,
        )

        post_process_ms = round((perf_counter() - post_process_start) * 1000, 2)

        logger.debug(
            "recommendation_benchmark_processing",
            embedding_fetch_ms=embedding_fetch_ms,
            bulk_session_load_ms=bulk_session_load_ms,
            scoring_ms=scoring_ms,
            post_process_ms=post_process_ms,
            total_ms=round((perf_counter() - benchmark_start) * 1000, 2),
            input_results=len(chroma_results),
            output_results=len(recommendations),
            used_bulk_load=bool(sessions_by_id),
        )

        return recommendations

    async def _recommend_fallback(
        self,
        db: Session,
        params: RecommendationQueryParams,
        limit: int = 10,
        popularity_sort: bool = False,
    ) -> list[tuple]:
        try:
            benchmark_start = perf_counter()
            soft = self._get_effective_soft_filters(params)
            exclude_ids = params.accepted_ids + params.rejected_ids
            sessions = session_crud.list_with_filters(
                db=db,
                limit=limit,
                status=SessionStatus.PUBLISHED,
                event_id=params.event_id,
                session_format=None if "session_format" in soft else params.session_format,
                tags=None if "tags" in soft else params.tags,
                location_cities=None if "location" in soft else params.location_cities,
                location_names=None if "location" in soft else params.location_names,
                language=None if "language" in soft else params.language,
                duration_min=None if "duration" in soft else params.duration_min,
                duration_max=None if "duration" in soft else params.duration_max,
                time_windows=None if "time_windows" in soft else params.time_windows,
                exclude_ids=exclude_ids,
                randomize=True,
                popularity_sort=popularity_sort,
            )

            preference_prefetch_start = perf_counter()
            preference_embeddings = await self._prefetch_preference_embeddings(
                accepted_ids=params.accepted_ids,
                rejected_ids=params.rejected_ids,
            )
            preference_prefetch_ms = round((perf_counter() - preference_prefetch_start) * 1000, 2)

            embedding_fetch_start = perf_counter()
            sessions_by_embedding_id = await self._fetch_embeddings_for_session_ids(
                [session.id for session in sessions]
            )
            embedding_fetch_ms = round((perf_counter() - embedding_fetch_start) * 1000, 2)
            embedding_required = bool(preference_embeddings["liked"]) or bool(
                preference_embeddings["disliked"]
            )

            from app.crud.session_popularity import session_popularity_crud

            fallback_session_ids = [s.id for s in sessions]
            popularity_map = session_popularity_crud.get_popularity_map(
                db=db, session_ids=fallback_session_ids, event_id=params.event_id
            )
            event_max_acceptance = (
                session_popularity_crud.get_event_max_acceptance(db=db, event_id=params.event_id)
                if params.popularity_weight > 0
                else 0
            )

            recommendations: list[tuple] = []
            scoring_start = perf_counter()
            for session in sessions:
                session_embedding = sessions_by_embedding_id.get(f"session_{session.id}")
                if session_embedding is None:
                    if embedding_required:
                        logger.debug("session_embedding_not_found", session_id=session.id)
                    session_embedding = self._get_default_embedding()

                pop_data = popularity_map.get(session.id, {})
                popularity_score = (
                    session_popularity_crud.compute_popularity_score(
                        pop_data.get("acceptance_count", 0), event_max_acceptance
                    )
                    if params.popularity_weight > 0
                    else None
                )

                soft_compliance = self._compute_soft_filter_compliance(session, params)
                scores = await self._compute_recommendation_scores(
                    session_embedding=session_embedding,
                    chroma_similarity=0.0,
                    semantic_similarity_enabled=False,
                    liked_embeddings=preference_embeddings["liked"],
                    disliked_embeddings=preference_embeddings["disliked"],
                    liked_embedding_weight=params.liked_embedding_weight,
                    disliked_embedding_weight=params.disliked_embedding_weight,
                    filter_compliance_score=soft_compliance,
                    filter_margin_weight=params.filter_margin_weight,
                    popularity_score=popularity_score,
                    popularity_weight=params.popularity_weight,
                )
                recommendations.append((session, scores))
            scoring_ms = round((perf_counter() - scoring_start) * 1000, 2)

            post_process_start = perf_counter()
            recommendations = self._finalize_recommendations(
                recommendations=recommendations,
                limit=limit,
                params=params,
            )
            post_process_ms = round((perf_counter() - post_process_start) * 1000, 2)

            logger.debug(
                "recommendation_crud_completed",
                rejected_ids_count=len(params.rejected_ids),
                recommendations=len(recommendations),
                limit=limit,
                soft_filters=list(soft),
                preference_prefetch_ms=preference_prefetch_ms,
                embedding_fetch_ms=embedding_fetch_ms,
                scoring_ms=scoring_ms,
                post_process_ms=post_process_ms,
                total_ms=round((perf_counter() - benchmark_start) * 1000, 2),
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
        popularity_score: float | None = None,
        popularity_weight: float = 0.0,
    ) -> dict[str, float | None]:
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None
        liked_cluster_sim = self._compute_liked_similarity(session_embedding, liked_embeddings)
        disliked_sim = self._compute_disliked_similarity(session_embedding, disliked_embeddings)

        components, component_weights = self.score_engine.build_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            filter_compliance_score=filter_compliance_score,
            popularity_score=popularity_score,
            weights={
                "liked": liked_embedding_weight,
                "disliked": disliked_embedding_weight,
                "compliance": filter_margin_weight,
                "popularity": popularity_weight,
            },
        )
        overall_score = self.score_engine.calculate_overall_score(components, component_weights)

        return {
            "overall_score": overall_score,
            "semantic_similarity": semantic_sim,
            "liked_cluster_similarity": liked_cluster_sim,
            "disliked_similarity": disliked_sim,
            "filter_compliance_score": filter_compliance_score,
            "popularity_score": popularity_score,
            "diversity_score": None,
        }
