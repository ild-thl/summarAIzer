"""Score composition helpers for recommendation ranking."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class _ScoreInputs:
    """Container for score inputs used by recommendation strategies."""

    semantic_sim: float | None
    liked_cluster_sim: float | None
    disliked_sim: float | None
    filter_compliance_score: float | None


class RecommendationScoreEngine:
    """Composable scoring engine for recommendation rank strategies."""

    STRATEGY_ORDER = ("semantic", "liked", "disliked", "compliance")

    @staticmethod
    def _semantic_component(score_inputs: _ScoreInputs, _weights: dict[str, float]) -> tuple | None:
        if score_inputs.semantic_sim is None:
            return None
        semantic = score_inputs.semantic_sim
        return semantic, 1.0

    @staticmethod
    def _liked_component(score_inputs: _ScoreInputs, weights: dict[str, float]) -> tuple | None:
        liked_cluster_sim = score_inputs.liked_cluster_sim
        weight = weights["liked"]
        if liked_cluster_sim is None or weight <= 0:
            return None
        return (
            liked_cluster_sim,
            weight,
        )

    @staticmethod
    def _disliked_component(score_inputs: _ScoreInputs, weights: dict[str, float]) -> tuple | None:
        disliked_sim = score_inputs.disliked_sim
        weight = weights["disliked"]
        if disliked_sim is None or weight <= 0:
            return None
        inverted_disliked = 1.0 - disliked_sim
        return (
            inverted_disliked,
            weight,
        )

    @staticmethod
    def _compliance_component(
        score_inputs: _ScoreInputs, weights: dict[str, float]
    ) -> tuple | None:
        filter_compliance_score = score_inputs.filter_compliance_score
        weight = weights["compliance"]
        if filter_compliance_score is None or weight <= 0:
            return None
        return (
            filter_compliance_score,
            weight,
        )

    @classmethod
    def strategy_registry(cls) -> dict[str, Callable]:
        """Return strategy registry keyed by strategy name for easy extension."""
        return {
            "semantic": cls._semantic_component,
            "liked": cls._liked_component,
            "disliked": cls._disliked_component,
            "compliance": cls._compliance_component,
        }

    def build_components(
        self,
        semantic_sim: float | None,
        liked_cluster_sim: float | None,
        disliked_sim: float | None,
        filter_compliance_score: float | None,
        weights: dict[str, float],
    ) -> tuple[list, list, list]:
        """Build component vectors for weighted aggregation."""
        components: list[float] = []
        component_weights: list[float] = []

        score_inputs = _ScoreInputs(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            filter_compliance_score=filter_compliance_score,
        )

        registry = self.strategy_registry()
        for strategy_name in self.STRATEGY_ORDER:
            strategy = registry[strategy_name]
            component_result = strategy(score_inputs, weights)
            if component_result is None:
                continue
            component, component_weight = component_result
            components.append(component)
            component_weights.append(component_weight)

        return components, component_weights

    @staticmethod
    def calculate_overall_score(components: list[float], weights: list[float]) -> float:
        """Calculate normalized weighted score in [0, 1]."""
        if not components:
            return 0.5

        weighted_sum = sum(c * w for c, w in zip(components, weights, strict=False))
        total_weight = sum(weights)
        overall_score = weighted_sum / total_weight if total_weight > 0 else 0.5
        return max(0.0, min(1.0, overall_score))
