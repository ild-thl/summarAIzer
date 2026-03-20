"""Unit tests for EmbeddingSearchService helper methods and internal logic.

These tests focus on testing simpler, isolatable helper methods that can be
tested without complex database fixtures, complementing the 78 integration tests.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
class TestBatchFetchEmbeddings:
    """Test the _batch_fetch_embeddings helper method."""

    async def test_batch_fetch_embeddings_with_results(self):
        """Test successful batch embedding fetch with multiple results."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        service.embedding_service = MagicMock()

        # Mock Chroma results (session_id, score, metadata)
        chroma_results_hard = [(1, 0.95, None), (2, 0.90, None)]
        chroma_results_soft = [(3, 0.85, None)]

        # Mock returned embeddings
        mock_embeddings = {
            1: [0.1, 0.2, 0.3],
            2: [0.4, 0.5, 0.6],
            3: [0.7, 0.8, 0.9],
        }

        service.embedding_service.get_session_embeddings = AsyncMock(return_value=mock_embeddings)

        result = await EmbeddingSearchService._batch_fetch_embeddings(
            service, chroma_results_hard, chroma_results_soft
        )

        assert "session_1" in result
        assert "session_2" in result
        assert "session_3" in result
        assert result["session_1"] == [0.1, 0.2, 0.3]

    async def test_batch_fetch_embeddings_empty_results(self):
        """Test batch fetch returns empty dict when no results provided."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        service.embedding_service = MagicMock()

        result = await EmbeddingSearchService._batch_fetch_embeddings(service, [], [])

        assert result == {}
        service.embedding_service.get_session_embeddings.assert_not_called()

    async def test_batch_fetch_embeddings_handles_exception(self):
        """Test batch fetch handles embedding service exceptions gracefully."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        service.embedding_service = MagicMock()
        service.embedding_service.get_session_embeddings = AsyncMock(
            side_effect=Exception("Service unavailable")
        )

        chroma_results_hard = [(1, 0.95, None)]

        result = await EmbeddingSearchService._batch_fetch_embeddings(
            service, chroma_results_hard, []
        )

        assert result == {}


class TestFilterCheckMethods:
    """Test individual filter check helper methods."""

    def test_check_format_match(self):
        """Test format filter when format matches."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock()
        session.session_format = MagicMock(value="workshop")

        result = EmbeddingSearchService._check_format(service, session, "workshop")
        assert result is True

    def test_check_format_mismatch(self):
        """Test format filter when format doesn't match."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock()
        session.session_format = MagicMock(value="talk")

        result = EmbeddingSearchService._check_format(service, session, "workshop")
        assert result is False

    def test_check_format_none_filter(self):
        """Test format filter with None (no filter)."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock()

        result = EmbeddingSearchService._check_format(service, session, None)
        assert result is False

    def test_check_language_match(self):
        """Test language filter when language matches."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(language="en")

        result = EmbeddingSearchService._check_language(service, session, "en")
        assert result is True

    def test_check_language_mismatch(self):
        """Test language filter when language doesn't match."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(language="en")

        result = EmbeddingSearchService._check_language(service, session, "de")
        assert result is False

    def test_check_duration_min_passes(self):
        """Test minimum duration check when duration is sufficient."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(duration=60)

        result = EmbeddingSearchService._check_duration_min(service, session, 30)
        assert result is True

    def test_check_duration_min_fails(self):
        """Test minimum duration check when duration is insufficient."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(duration=60)

        result = EmbeddingSearchService._check_duration_min(service, session, 90)
        assert result is False

    def test_check_duration_max_passes(self):
        """Test maximum duration check when duration is within limit."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(duration=60)

        result = EmbeddingSearchService._check_duration_max(service, session, 90)
        assert result is True

    def test_check_duration_max_fails(self):
        """Test maximum duration check when duration exceeds limit."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(duration=60)

        result = EmbeddingSearchService._check_duration_max(service, session, 30)
        assert result is False

    def test_check_tags_with_match(self):
        """Test tags filter with matching tag (OR logic)."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(tags=["python", "data"])

        result = EmbeddingSearchService._check_tags(service, session, ["python", "javascript"])
        assert result is True

    def test_check_tags_no_match(self):
        """Test tags filter with no matching tags."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(tags=["python"])

        result = EmbeddingSearchService._check_tags(service, session, ["javascript", "ruby"])
        assert result is False

    def test_check_location_match(self):
        """Test location filter with matching location."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(location="Room A")

        result = EmbeddingSearchService._check_location(service, session, ["Room A", "Room B"])
        assert result is True

    def test_check_location_no_match(self):
        """Test location filter with no matching location."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        session = MagicMock(location="Room C")

        result = EmbeddingSearchService._check_location(service, session, ["Room A", "Room B"])
        assert result is False

    def test_check_time_windows_passes(self):
        """Test time window check when session fits within provided window."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = EmbeddingSearchService(MagicMock())
        now = datetime.utcnow()
        session = MagicMock(start_datetime=now, end_datetime=now + timedelta(minutes=30))

        window = {"start": now - timedelta(hours=1), "end": now + timedelta(hours=1)}
        result = EmbeddingSearchService._check_time_windows(service, session, [window])
        assert result is True

    def test_check_time_windows_fails(self):
        """Test time window check when session does not fit window."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = EmbeddingSearchService(MagicMock())
        now = datetime.utcnow()
        session = MagicMock(start_datetime=now, end_datetime=now + timedelta(minutes=30))

        window = {"start": now + timedelta(hours=1), "end": now + timedelta(hours=2)}
        result = EmbeddingSearchService._check_time_windows(service, session, [window])
        assert result is False


class TestComputeLikedSimilarity:
    """Test _compute_liked_similarity helper (Phase 2 re-ranking)."""

    def test_liked_similarity_empty_embeddings(self):
        """Test liked similarity with no liked embeddings."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        query_embedding = [0.1, 0.2, 0.3]

        similarity = EmbeddingSearchService._compute_liked_similarity(service, query_embedding, {})

        # Should return 0 or None when empty
        assert similarity == 0.0 or similarity is None


class TestComputeDislikedSimilarity:
    """Test _compute_disliked_similarity helper (Phase 2 de-ranking)."""

    def test_disliked_similarity_empty_embeddings(self):
        """Test disliked similarity with no disliked embeddings."""
        from app.services.embedding_search_service import EmbeddingSearchService

        service = MagicMock(spec=EmbeddingSearchService)
        query_embedding = [0.1, 0.2, 0.3]

        similarity = EmbeddingSearchService._compute_disliked_similarity(
            service, query_embedding, {}
        )

        # Should return 0 or None when empty
        assert similarity == 0.0 or similarity is None
