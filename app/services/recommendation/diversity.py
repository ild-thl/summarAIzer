"""Diversity-aware re-ranking for recommendation results."""

from typing import Any


class RecommendationDiversityOptimizer:
    """Greedy MMR-style optimizer using metadata-only diversity with candidate pre-pruning.

    Caps the candidate pool at MAX_CANDIDATES before the greedy loop to bound
    worst-case complexity at O(MAX_CANDIDATES * limit).
    """

    # Maximum candidates to consider before the greedy loop.
    MAX_CANDIDATES = 100

    @staticmethod
    def _normalize_metadata_values(values: Any) -> set[str]:
        """Normalize optional metadata values into a set of strings."""
        if values is None:
            return set()
        if isinstance(values, str):
            return {values} if values else set()
        if isinstance(values, list | tuple | set):
            return {str(value) for value in values if value is not None and str(value)}
        return set()

    @staticmethod
    def _extract_metadata_sets(session: Any) -> dict[str, set[str]]:
        """Extract categorical metadata from a session as sets of string values."""
        metadata: dict[str, set[str]] = {}

        tags = RecommendationDiversityOptimizer._normalize_metadata_values(
            getattr(session, "tags", None)
        )
        if tags:
            metadata["tags"] = tags

        fmt = getattr(session, "session_format", None)
        if fmt is not None:
            metadata["session_format"] = {fmt.value if hasattr(fmt, "value") else str(fmt)}

        lang = getattr(session, "language", None)
        if lang:
            metadata["language"] = {str(lang)}

        speakers = RecommendationDiversityOptimizer._normalize_metadata_values(
            getattr(session, "speakers", None)
        )
        if speakers:
            metadata["speakers"] = speakers

        return metadata

    @staticmethod
    def _compute_metadata_coverage_bonus(
        session_metadata: dict[str, set[str]],
        selected_coverage: dict[str, dict[str, int]],
        active_filter_values: dict[str, set[str]] | None,
    ) -> float:
        """Compute how much new categorical diversity a candidate adds.

        For each metadata attribute, measure the ratio of values this candidate
        introduces that are underrepresented among already-selected results.
        When active filter values are provided for an attribute, only track
        coverage of those specific values.
        """
        if not session_metadata:
            return 0.0

        attribute_bonuses: list[float] = []

        for attr, values in session_metadata.items():
            if not values:
                continue

            coverage = selected_coverage.get(attr, {})

            # When filter values are active for this attribute, only care about those
            filter_vals = active_filter_values.get(attr) if active_filter_values else None
            relevant_values = values & filter_vals if filter_vals else values

            if not relevant_values:
                continue

            if not coverage:
                # Nothing selected yet — any value is maximally novel
                attribute_bonuses.append(1.0)
                continue

            total_selected = sum(coverage.values()) or 1
            novelty_scores: list[float] = []
            for val in relevant_values:
                count = coverage.get(val, 0)
                # Inverse frequency: less represented values score higher
                novelty_scores.append(1.0 - (count / total_selected))

            attribute_bonuses.append(max(novelty_scores) if novelty_scores else 0.0)

        return sum(attribute_bonuses) / len(attribute_bonuses) if attribute_bonuses else 0.0

    @staticmethod
    def _update_coverage(
        selected_coverage: dict[str, dict[str, int]],
        session_metadata: dict[str, set[str]],
    ) -> None:
        """Update coverage counters after selecting a candidate."""
        for attr, values in session_metadata.items():
            if attr not in selected_coverage:
                selected_coverage[attr] = {}
            for val in values:
                selected_coverage[attr][val] = selected_coverage[attr].get(val, 0) + 1

    @staticmethod
    def _build_active_filter_values(
        session_format: list[str] | None,
        tags: list[str] | None,
        language: list[str] | None,
    ) -> dict[str, set[str]] | None:
        """Build a map of filter attribute -> set of requested values."""
        active: dict[str, set[str]] = {}
        if session_format:
            active["session_format"] = set(session_format)
        if tags:
            active["tags"] = set(tags)
        if language:
            active["language"] = set(language)
        return active if active else None

    def diversify_results(
        self,
        candidates: list[tuple[Any, dict[str, Any]]],
        limit: int,
        diversity_weight: float,
        session_format: list[str] | None = None,
        tags: list[str] | None = None,
        language: list[str] | None = None,
    ) -> list[tuple[Any, dict[str, Any]]]:
        """Re-rank candidates using greedy metadata-diversity-aware selection.

        The candidate pool is capped at MAX_CANDIDATES (taken from the head of the
        pre-sorted list) before the greedy loop, bounding complexity at
        O(MAX_CANDIDATES * limit).

        Args:
            candidates: List of (session, scores_dict) tuples, pre-sorted by overall_score.
            limit: Maximum results to return.
            diversity_weight: 0.0 = pure relevance, 1.0 = pure diversity.
            session_format: Active session_format filter values (for targeted coverage).
            tags: Active tag filter values (for targeted coverage).
            language: Active language filter values (for targeted coverage).

        Returns:
            Re-ranked list of (session, scores_dict) with diversity_score populated.
        """
        if diversity_weight <= 0.0 or not candidates:
            # Pure relevance: keep existing order, just add diversity_score=None
            result = []
            for session, scores in candidates[:limit]:
                scores_copy = dict(scores)
                scores_copy["diversity_score"] = None
                result.append((session, scores_copy))
            return result

        # Cap the pool to bound O(n * limit) cost
        pool = candidates[: self.MAX_CANDIDATES]
        active_filter_values = self._build_active_filter_values(session_format, tags, language)

        # Pre-compute metadata once per candidate to avoid repeated extraction in the inner loop
        metadata_cache = {i: self._extract_metadata_sets(pool[i][0]) for i in range(len(pool))}

        remaining = list(range(len(pool)))
        selected_indices: list[int] = []
        selected_coverage: dict[str, dict[str, int]] = {}
        diversity_scores: dict[int, float] = {}

        for _ in range(min(limit, len(pool))):
            best_idx = -1
            best_combined = -1.0
            best_diversity_bonus = 0.0

            for idx in remaining:
                _, scores = pool[idx]
                relevance = scores["overall_score"]

                diversity_bonus = self._compute_metadata_coverage_bonus(
                    metadata_cache[idx], selected_coverage, active_filter_values
                )
                combined = (1.0 - diversity_weight) * relevance + diversity_weight * diversity_bonus

                if combined > best_combined:
                    best_combined = combined
                    best_idx = idx
                    best_diversity_bonus = diversity_bonus

            if best_idx < 0:
                break

            selected_indices.append(best_idx)
            remaining.remove(best_idx)
            diversity_scores[best_idx] = round(best_diversity_bonus, 3)

            self._update_coverage(selected_coverage, metadata_cache[best_idx])

        result: list[tuple[Any, dict[str, Any]]] = []
        for idx in selected_indices:
            session, scores = pool[idx]
            scores_copy = dict(scores)
            scores_copy["diversity_score"] = diversity_scores.get(idx)
            result.append((session, scores_copy))

        return result
