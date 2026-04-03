"""Unit tests for the diversity-aware recommendation re-ranking."""

from types import SimpleNamespace

from app.services.recommendation.diversity import RecommendationDiversityOptimizer


def _make_session(
    session_id: int,
    tags: list[str] | None = None,
    session_format: str | None = None,
    language: str = "en",
    speakers: list[str] | None = None,
) -> SimpleNamespace:
    """Create a minimal session-like object for testing."""
    fmt = None
    if session_format:
        fmt = SimpleNamespace(value=session_format)
    return SimpleNamespace(
        id=session_id,
        tags=tags,
        session_format=fmt,
        language=language,
        speakers=speakers,
    )


def _make_scores(overall: float, **extra) -> dict:
    return {
        "overall_score": overall,
        "semantic_similarity": extra.get("semantic_similarity"),
        "liked_cluster_similarity": extra.get("liked_cluster_similarity"),
        "disliked_similarity": extra.get("disliked_similarity"),
        "filter_compliance_score": extra.get("filter_compliance_score"),
        "diversity_score": None,
    }


class TestDiversityWeightZero:
    """When diversity_weight=0, behavior matches pure relevance ranking."""

    def test_preserves_input_order(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.9)),
            (_make_session(2, tags=["ai"]), _make_scores(0.8)),
            (_make_session(3, tags=["bio"]), _make_scores(0.7)),
        ]
        result = optimizer.diversify_results(candidates, limit=3, diversity_weight=0.0)

        assert [s.id for s, _ in result] == [1, 2, 3]
        assert all(s["diversity_score"] is None for _, s in result)

    def test_respects_limit(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(i, tags=["ai"]), _make_scores(1.0 - i * 0.1)) for i in range(10)
        ]
        result = optimizer.diversify_results(candidates, limit=3, diversity_weight=0.0)
        assert len(result) == 3

    def test_empty_candidates(self):
        optimizer = RecommendationDiversityOptimizer()
        result = optimizer.diversify_results([], limit=5, diversity_weight=0.0)
        assert result == []


class TestTagDiversity:
    """Diversity promotes underrepresented tags."""

    def test_balances_two_tags(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.95)),
            (_make_session(2, tags=["ai"]), _make_scores(0.90)),
            (_make_session(3, tags=["ai"]), _make_scores(0.85)),
            (_make_session(4, tags=["sustainability"]), _make_scores(0.80)),
            (_make_session(5, tags=["sustainability"]), _make_scores(0.75)),
        ]
        result = optimizer.diversify_results(
            candidates,
            limit=4,
            diversity_weight=0.4,
            tags=["ai", "sustainability"],
        )

        selected_ids = [s.id for s, _ in result]
        selected_tags = [s.tags[0] for s, _ in result]
        # With diversity, both tags should be represented
        assert "ai" in selected_tags
        assert "sustainability" in selected_tags
        # Session 4 (sustainability, 0.80) should be promoted ahead of session 3 (ai, 0.85)
        assert 4 in selected_ids

    def test_active_tag_filter_focuses_coverage(self):
        """When tag filter is active, coverage tracks only those specific tags."""
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai", "ml"]), _make_scores(0.9)),
            (_make_session(2, tags=["ai", "ethics"]), _make_scores(0.85)),
            (_make_session(3, tags=["sustainability"]), _make_scores(0.8)),
        ]
        result = optimizer.diversify_results(
            candidates,
            limit=3,
            diversity_weight=0.5,
            tags=["ai", "sustainability"],
        )
        selected_ids = [s.id for s, _ in result]
        # Session 3 with "sustainability" should be promoted to cover the second filter tag
        assert 3 in selected_ids


class TestFormatDiversity:
    """Diversity promotes underrepresented session formats."""

    def test_balances_formats(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"], session_format="workshop"), _make_scores(0.90)),
            (_make_session(2, tags=["ai"], session_format="workshop"), _make_scores(0.88)),
            (_make_session(3, tags=["ai"], session_format="workshop"), _make_scores(0.85)),
            (_make_session(4, tags=["ai"], session_format="input"), _make_scores(0.82)),
        ]
        result = optimizer.diversify_results(
            candidates,
            limit=3,
            diversity_weight=0.6,
            session_format=["workshop", "input"],
        )
        selected_formats = [s.session_format.value for s, _ in result]
        assert "input" in selected_formats
        assert "workshop" in selected_formats


class TestLanguageDiversity:
    """Diversity promotes underrepresented languages."""

    def test_balances_languages(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, language="en"), _make_scores(0.95)),
            (_make_session(2, language="en"), _make_scores(0.90)),
            (_make_session(3, language="de"), _make_scores(0.80)),
            (_make_session(4, language="de"), _make_scores(0.75)),
        ]
        result = optimizer.diversify_results(
            candidates,
            limit=3,
            diversity_weight=0.4,
            language=["en", "de"],
        )
        selected_languages = [s.language for s, _ in result]
        assert "en" in selected_languages
        assert "de" in selected_languages


class TestEmbeddingDiversity:
    """Embedding diversity is disabled; diversity now relies purely on metadata."""

    def test_embedding_diversity_disabled_metadata_only(self):
        """With EMBEDDING_DIVERSITY_RATIO=0, selection ignores embedding similarity."""
        optimizer = RecommendationDiversityOptimizer()
        # Sessions with identical tags; selection based on relevance + metadata coverage (empty here)
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.90)),
            (_make_session(2, tags=["ai"]), _make_scores(0.88)),
            (_make_session(3, tags=["ai"]), _make_scores(0.85)),
        ]
        embeddings = {
            "session_1": [1.0, 0.0, 0.0],
            "session_2": [0.99, 0.01, 0.0],  # Near-identical to session 1
            "session_3": [0.0, 1.0, 0.0],  # Orthogonal to session 1
        }
        result = optimizer.diversify_results(
            candidates,
            limit=2,
            diversity_weight=0.6,
            embeddings_map=embeddings,
        )
        selected_ids = [s.id for s, _ in result]
        # With metadata-only diversity and all sessions having same tags,
        # selection falls back to relevance ranking (0.90, 0.88)
        assert selected_ids == [1, 2]

    def test_no_embeddings_gracefully_handled(self):
        """When no embeddings available, embedding diversity defaults to 1.0 for all (ignored now)."""
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.9)),
            (_make_session(2, tags=["bio"]), _make_scores(0.8)),
        ]
        result = optimizer.diversify_results(
            candidates, limit=2, diversity_weight=0.3, embeddings_map=None
        )
        assert len(result) == 2


class TestCombinedDiversity:
    """Test metadata + embedding diversity interaction."""

    def test_combined_diversity_promotes_variety(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (
                _make_session(1, tags=["ai"], session_format="workshop", language="en"),
                _make_scores(0.90),
            ),
            (
                _make_session(2, tags=["ai"], session_format="workshop", language="en"),
                _make_scores(0.88),
            ),
            (
                _make_session(3, tags=["bio"], session_format="input", language="de"),
                _make_scores(0.75),
            ),
        ]
        embeddings = {
            "session_1": [1.0, 0.0],
            "session_2": [0.98, 0.02],
            "session_3": [0.0, 1.0],
        }
        result = optimizer.diversify_results(
            candidates,
            limit=2,
            diversity_weight=0.6,
            embeddings_map=embeddings,
            tags=["ai", "bio"],
            session_format=["workshop", "input"],
            language=["en", "de"],
        )
        selected_ids = [s.id for s, _ in result]
        # Despite lower score, session 3 provides huge diversity across all dimensions
        assert 3 in selected_ids


class TestDiversityScoreField:
    """Verify diversity_score is populated in output."""

    def test_diversity_score_populated_when_active(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.9)),
            (_make_session(2, tags=["bio"]), _make_scores(0.8)),
        ]
        result = optimizer.diversify_results(candidates, limit=2, diversity_weight=0.3)
        for _, scores in result:
            assert "diversity_score" in scores
            assert scores["diversity_score"] is not None
            assert 0.0 <= scores["diversity_score"] <= 1.0

    def test_diversity_score_none_when_weight_zero(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.9)),
        ]
        result = optimizer.diversify_results(candidates, limit=1, diversity_weight=0.0)
        assert result[0][1]["diversity_score"] is None


class TestEdgeCases:
    """Edge cases for the diversity optimizer."""

    def test_single_candidate(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [(_make_session(1, tags=["ai"]), _make_scores(0.9))]
        result = optimizer.diversify_results(candidates, limit=5, diversity_weight=0.5)
        assert len(result) == 1
        assert result[0][0].id == 1

    def test_limit_larger_than_candidates(self):
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.9)),
            (_make_session(2, tags=["bio"]), _make_scores(0.8)),
        ]
        result = optimizer.diversify_results(candidates, limit=10, diversity_weight=0.3)
        assert len(result) == 2

    def test_all_identical_metadata(self):
        """All candidates have same metadata — relies on embedding diversity only."""
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (
                _make_session(1, tags=["ai"], session_format="workshop", language="en"),
                _make_scores(0.9),
            ),
            (
                _make_session(2, tags=["ai"], session_format="workshop", language="en"),
                _make_scores(0.8),
            ),
            (
                _make_session(3, tags=["ai"], session_format="workshop", language="en"),
                _make_scores(0.7),
            ),
        ]
        result = optimizer.diversify_results(candidates, limit=3, diversity_weight=0.3)
        assert len(result) == 3

    def test_sessions_without_metadata(self):
        """Sessions with None tags/format still work."""
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1), _make_scores(0.9)),
            (_make_session(2), _make_scores(0.8)),
        ]
        result = optimizer.diversify_results(candidates, limit=2, diversity_weight=0.5)
        assert len(result) == 2

    def test_high_diversity_weight_reorders_significantly(self):
        """Very high diversity_weight should strongly prefer diverse candidates."""
        optimizer = RecommendationDiversityOptimizer()
        candidates = [
            (_make_session(1, tags=["ai"]), _make_scores(0.99)),
            (_make_session(2, tags=["ai"]), _make_scores(0.98)),
            (_make_session(3, tags=["ai"]), _make_scores(0.97)),
            (_make_session(4, tags=["bio"]), _make_scores(0.50)),
        ]
        result = optimizer.diversify_results(
            candidates, limit=2, diversity_weight=0.9, tags=["ai", "bio"]
        )
        selected_ids = [s.id for s, _ in result]
        # Even with score 0.50, "bio" session should make it in with weight=0.9
        assert 4 in selected_ids
