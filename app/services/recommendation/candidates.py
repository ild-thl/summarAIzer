"""Candidate retrieval helpers for recommendation search passes."""

from collections.abc import Callable
from typing import Any


class RecommendationCandidateCollector:
    """Collect and deduplicate soft-pass recommendation candidates."""

    def __init__(
        self,
        search_similar_sessions: Callable,
        combine_conditions: Callable,
        build_time_windows_condition: Callable,
    ):
        self.search_similar_sessions = search_similar_sessions
        self.combine_conditions = combine_conditions
        self.build_time_windows_condition = build_time_windows_condition

    async def collect_soft_pass_candidates(
        self,
        query_embedding: list[float],
        soft_search_limit: int,
        nin_condition: dict | None,
        time_windows: list[Any] | None,
    ) -> list:
        """Collect soft-pass candidates, splitting requests by window when provided."""
        if not time_windows:
            chroma_where_soft = self.combine_conditions(nin_condition, None)
            if chroma_where_soft:
                return await self.search_similar_sessions(
                    query_embedding, limit=soft_search_limit, where=chroma_where_soft
                )
            return await self.search_similar_sessions(query_embedding, limit=soft_search_limit)

        # Distribute retrieval budget across windows to avoid one window dominating.
        per_window_limit = max(1, soft_search_limit // len(time_windows))
        collected_results = []
        for window in time_windows:
            window_condition = self.build_time_windows_condition([window])
            chroma_where_soft = self.combine_conditions(nin_condition, window_condition)
            if chroma_where_soft:
                results = await self.search_similar_sessions(
                    query_embedding,
                    limit=per_window_limit,
                    where=chroma_where_soft,
                )
            else:
                results = await self.search_similar_sessions(
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
        deduped_results = list(deduped.values())
        deduped_results.sort(key=lambda item: item[1], reverse=True)
        return deduped_results[:soft_search_limit]
