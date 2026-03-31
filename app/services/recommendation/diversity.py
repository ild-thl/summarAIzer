"""Diversity-aware re-ranking for recommendation results."""

from typing import Any

import numpy as np


class RecommendationDiversityOptimizer:
    """Greedy MMR-style optimizer combining metadata coverage and embedding dissimilarity."""

    METADATA_DIVERSITY_RATIO = 0.6
    EMBEDDING_DIVERSITY_RATIO = 0.4

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        v1 = np.asarray(vec1, dtype=np.float32).flatten()
        v2 = np.asarray(vec2, dtype=np.float32).flatten()
        dot_product = float(np.dot(v1, v2))
        norm_v1 = float(np.linalg.norm(v1))
        norm_v2 = float(np.linalg.norm(v2))
        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0
        cosine_sim = dot_product / (norm_v1 * norm_v2)
        return float(max(0.0, (cosine_sim + 1.0) / 2.0))

    @staticmethod
    def _extract_metadata_sets(session: Any) -> dict[str, set[str]]:
        """Extract categorical metadata from a session as sets of string values."""
        metadata: dict[str, set[str]] = {}

        tags = getattr(session, "tags", None)
        if tags:
            metadata["tags"] = set(tags)

        fmt = getattr(session, "session_format", None)
        if fmt is not None:
            metadata["session_format"] = {fmt.value if hasattr(fmt, "value") else str(fmt)}

        lang = getattr(session, "language", None)
        if lang:
            metadata["language"] = {str(lang)}

        speakers = getattr(session, "speakers", None)
        if speakers:
            metadata["speakers"] = set(speakers)

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
            if filter_vals:
                relevant_values = values & filter_vals
            else:
                relevant_values = values

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
    def _compute_embedding_diversity(
        candidate_embedding: list[float] | None,
        selected_embeddings: list[list[float]],
        cosine_fn,
    ) -> float:
        """1 - max similarity to already-selected embeddings."""
        if candidate_embedding is None or not selected_embeddings:
            return 1.0

        max_sim = max(cosine_fn(candidate_embedding, sel_emb) for sel_emb in selected_embeddings)
        return 1.0 - max_sim

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
        embeddings_map: dict[str, list[float]] | None = None,
        session_format: list[str] | None = None,
        tags: list[str] | None = None,
        language: list[str] | None = None,
    ) -> list[tuple[Any, dict[str, Any]]]:
        """Re-rank candidates using greedy diversity-aware selection.

        Args:
            candidates: List of (session, scores_dict) tuples, pre-sorted by overall_score.
            limit: Maximum results to return.
            diversity_weight: 0.0 = pure relevance, 1.0 = pure diversity.
            embeddings_map: Map of "session_{id}" -> embedding vector.
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

        embeddings_map = embeddings_map or {}
        active_filter_values = self._build_active_filter_values(session_format, tags, language)

        remaining = list(range(len(candidates)))
        selected_indices: list[int] = []
        selected_embeddings: list[list[float]] = []
        selected_coverage: dict[str, dict[str, int]] = {}
        diversity_scores: dict[int, float] = {}

        for _ in range(min(limit, len(candidates))):
            best_idx = -1
            best_combined = -1.0
            best_diversity_bonus = 0.0

            for idx in remaining:
                session, scores = candidates[idx]

                # Use overall_score directly (already in [0, 1])
                relevance = scores["overall_score"]

                # Metadata diversity
                session_metadata = self._extract_metadata_sets(session)
                metadata_bonus = self._compute_metadata_coverage_bonus(
                    session_metadata, selected_coverage, active_filter_values
                )

                # Embedding diversity
                emb_key = f"session_{session.id}"
                candidate_embedding = embeddings_map.get(emb_key)
                embedding_bonus = self._compute_embedding_diversity(
                    candidate_embedding, selected_embeddings, self._cosine_similarity
                )

                diversity_bonus = (
                    self.METADATA_DIVERSITY_RATIO * metadata_bonus
                    + self.EMBEDDING_DIVERSITY_RATIO * embedding_bonus
                )

                combined = (1.0 - diversity_weight) * relevance + diversity_weight * diversity_bonus

                if combined > best_combined:
                    best_combined = combined
                    best_idx = idx
                    best_diversity_bonus = diversity_bonus

            if best_idx < 0:
                break

            # Select this candidate
            selected_indices.append(best_idx)
            remaining.remove(best_idx)
            diversity_scores[best_idx] = round(best_diversity_bonus, 3)

            session, _ = candidates[best_idx]
            session_metadata = self._extract_metadata_sets(session)
            self._update_coverage(selected_coverage, session_metadata)

            emb_key = f"session_{session.id}"
            candidate_embedding = embeddings_map.get(emb_key)
            if candidate_embedding is not None:
                selected_embeddings.append(candidate_embedding)

        result: list[tuple[Any, dict[str, Any]]] = []
        for idx in selected_indices:
            session, scores = candidates[idx]
            scores_copy = dict(scores)
            scores_copy["diversity_score"] = diversity_scores.get(idx)
            result.append((session, scores_copy))

        return result
