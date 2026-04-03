"""Filter compliance helpers for recommendation soft mode."""

from typing import Any

from app.services.recommendation.planning import RecommendationPlanner


class RecommendationFilterEvaluator:
    """Evaluate how well a session matches active filters."""

    def __init__(self, planner: RecommendationPlanner):
        self.planner = planner

    @staticmethod
    def check_format(session, session_format: list[str] | None) -> bool:
        if not session_format:
            return False
        return bool(session.session_format and session.session_format.value in session_format)

    @staticmethod
    def check_language(session, language: list[str] | None) -> bool:
        if not language:
            return False
        return session.language in language

    @staticmethod
    def check_tags(session, tags: list[str] | None) -> bool:
        if not tags:
            return False
        session_tags_set = set(session.tags or [])
        return any(tag in session_tags_set for tag in tags)

    @staticmethod
    def check_location(
        session, location_cities: list[str] | None, location_names: list[str] | None
    ) -> bool:
        if not location_cities and not location_names:
            return False
        loc = session.location_rel
        if loc is None:
            return False
        if location_cities and loc.city in location_cities:
            return True
        if location_names and loc.name in location_names:
            return True
        return False

    @staticmethod
    def check_duration_min(session, duration_min: int | None) -> bool:
        if duration_min is None:
            return False
        return bool(session.duration is not None and session.duration >= duration_min)

    @staticmethod
    def check_duration_max(session, duration_max: int | None) -> bool:
        if duration_max is None:
            return False
        return bool(session.duration is not None and session.duration <= duration_max)

    def check_time_windows(self, session, time_windows: list[Any] | None) -> bool:
        if not time_windows:
            return False
        return self.planner.is_within_time_windows(session, time_windows)

    def compute_filter_compliance_score(
        self,
        session,
        session_format: list[str] | None,
        tags: list[str] | None,
        location_cities: list[str] | None,
        location_names: list[str] | None,
        language: list[str] | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
    ) -> float:
        """Compute ratio of matched active filters."""
        location_active = location_cities is not None or location_names is not None
        filter_checks = [
            (session_format is not None, self.check_format(session, session_format)),
            (language is not None, self.check_language(session, language)),
            (tags is not None, self.check_tags(session, tags)),
            (location_active, self.check_location(session, location_cities, location_names)),
            (duration_min is not None, self.check_duration_min(session, duration_min)),
            (duration_max is not None, self.check_duration_max(session, duration_max)),
            (time_windows is not None, self.check_time_windows(session, time_windows)),
        ]

        matched = sum(bool(check) for is_active, check in filter_checks if is_active)
        total = sum(1 for is_active, _ in filter_checks if is_active)
        return 1.0 if total == 0 else matched / total
