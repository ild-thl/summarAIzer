"""Planning helpers for recommendation plan mode."""

from datetime import datetime
from typing import Any

from app.utils.helpers import DateTimeUtils


class RecommendationPlanner:
    """Build non-overlapping recommendation schedules."""

    @staticmethod
    def _extract_window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
        """Extract (start, end) from TimeWindow objects or plain dicts."""
        if isinstance(window, dict):
            return window.get("start"), window.get("end")
        return getattr(window, "start", None), getattr(window, "end", None)

    def is_within_time_windows(self, session, time_windows: list[Any] | None) -> bool:
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

    @staticmethod
    def _has_required_break(session, selected_session, min_break_minutes: int) -> bool:
        """Check if candidate keeps minimum break distance from a selected session."""
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

    @staticmethod
    def _fits_gap_constraint(session, selected: list[tuple], max_gap_minutes: int | None) -> bool:
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

    def optimize_session_plan(
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
            if not self.is_within_time_windows(session, time_windows):
                continue
            if not self._fits_non_overlap_constraints(session, selected, min_break_minutes):
                continue
            if not self._fits_gap_constraint(session, selected, max_gap_minutes):
                continue

            plan_scores = dict(scores)
            selected.append((session, plan_scores))

            if len(selected) >= limit:
                break

        selected.sort(key=lambda item: (item[0].start_datetime, item[0].id))
        return selected
