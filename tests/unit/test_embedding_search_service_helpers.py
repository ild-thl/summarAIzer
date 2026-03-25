"""Unit tests for recommendation helper methods and internal logic."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.recommendation.service import RecommendationService


@pytest.mark.asyncio
class TestBatchFetchEmbeddings:
    """Test the _batch_fetch_embeddings helper method."""

    async def test_batch_fetch_embeddings_with_results(self):
        embedding_service = MagicMock()
        service = RecommendationService(embedding_service)

        chroma_results_hard = [(1, 0.95, None), (2, 0.90, None)]
        chroma_results_soft = [(3, 0.85, None)]
        mock_embeddings = {
            1: [0.1, 0.2, 0.3],
            2: [0.4, 0.5, 0.6],
            3: [0.7, 0.8, 0.9],
        }
        service.embedding_service.get_session_embeddings = AsyncMock(return_value=mock_embeddings)

        result = await service._batch_fetch_embeddings(chroma_results_hard, chroma_results_soft)

        assert result["session_1"] == [0.1, 0.2, 0.3]
        assert result["session_2"] == [0.4, 0.5, 0.6]
        assert result["session_3"] == [0.7, 0.8, 0.9]

    async def test_batch_fetch_embeddings_empty_results(self):
        service = RecommendationService(MagicMock())

        result = await service._batch_fetch_embeddings([], [])

        assert result == {}
        service.embedding_service.get_session_embeddings.assert_not_called()

    async def test_batch_fetch_embeddings_handles_exception(self):
        embedding_service = MagicMock()
        embedding_service.get_session_embeddings = AsyncMock(
            side_effect=Exception("Service unavailable")
        )
        service = RecommendationService(embedding_service)

        result = await service._batch_fetch_embeddings([(1, 0.95, None)], [])

        assert result == {}

    async def test_batch_fetch_embeddings_deduplicates_ids(self):
        embedding_service = MagicMock()
        embedding_service.get_session_embeddings = AsyncMock(return_value={48: [0.1, 0.2, 0.3]})
        service = RecommendationService(embedding_service)

        await service._batch_fetch_embeddings(
            chroma_results_hard=[(48, 0.95, None)],
            chroma_results_soft=[(48, 0.90, None)],
        )

        embedding_service.get_session_embeddings.assert_called_once_with([48])


class TestFilterCheckMethods:
    @pytest.fixture
    def service(self):
        return RecommendationService(MagicMock())

    def test_check_format_match(self, service):
        session = MagicMock()
        session.session_format = MagicMock(value="workshop")
        assert service.filter_evaluator.check_format(session, "workshop") is True

    def test_check_format_mismatch(self, service):
        session = MagicMock()
        session.session_format = MagicMock(value="talk")
        assert service.filter_evaluator.check_format(session, "workshop") is False

    def test_check_language_match(self, service):
        assert service.filter_evaluator.check_language(MagicMock(language="en"), "en") is True

    def test_check_duration_min_fails(self, service):
        assert service.filter_evaluator.check_duration_min(MagicMock(duration=60), 90) is False

    def test_check_tags_with_match(self, service):
        assert (
            service.filter_evaluator.check_tags(MagicMock(tags=["python"]), ["python", "go"])
            is True
        )

    def test_check_location_no_match(self, service):
        assert (
            service.filter_evaluator.check_location(MagicMock(location="Room C"), ["Room A"])
            is False
        )

    def test_check_time_windows_passes(self, service):
        now = datetime.utcnow()
        session = MagicMock(start_datetime=now, end_datetime=now + timedelta(minutes=30))
        window = {"start": now - timedelta(hours=1), "end": now + timedelta(hours=1)}
        assert service.filter_evaluator.check_time_windows(session, [window]) is True

    def test_filter_compliance_handles_missing_optional_fields(self, service):
        now = datetime.utcnow()
        session = MagicMock(
            session_format=None,
            language="en",
            tags=None,
            location=None,
            duration=None,
            start_datetime=now,
            end_datetime=now + timedelta(minutes=30),
        )

        score = service.filter_evaluator.compute_filter_compliance_score(
            session=session,
            session_format="workshop",
            tags=None,
            location=None,
            language="en",
            duration_min=None,
            duration_max=None,
            time_windows=[{"start": now - timedelta(hours=1), "end": now + timedelta(hours=1)}],
        )

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


class TestComputeSimilarities:
    @pytest.fixture
    def service(self):
        return RecommendationService(MagicMock())

    def test_liked_similarity_empty_embeddings(self, service):
        similarity = service._compute_liked_similarity([0.1, 0.2, 0.3], {})
        assert similarity is None

    def test_disliked_similarity_empty_embeddings(self, service):
        similarity = service._compute_disliked_similarity([0.1, 0.2, 0.3], {})
        assert similarity is None
