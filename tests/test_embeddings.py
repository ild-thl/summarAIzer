"""
Unit tests for embedding and semantic search features.

Tests cover:
- EmbeddingService: embedding generation and Chroma storage/retrieval
- EmbeddingSearchService: search orchestration workflow
- Integration tests: end-to-end search pipeline
- Generic/refactored components: unified embedding and search infrastructure
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

from sqlalchemy.orm import Session
from app.services.embedding_service import EmbeddingService
from app.services.embedding_search_service import EmbeddingSearchService
from app.database.models import SessionStatus, EventStatus


# ============================================================================
# EmbeddingService Tests
# ============================================================================


class TestEmbeddingService:
    """Test low-level embedding operations."""

    @pytest.fixture
    def mock_chroma_client(self):
        """Create mock Chroma client."""
        client = MagicMock()
        sessions_collection = MagicMock()
        events_collection = MagicMock()
        client.get_or_create_collection = MagicMock(
            side_effect=[sessions_collection, events_collection]
        )
        return client

    @pytest.fixture
    def embedding_service(self, mock_chroma_client):
        """Create EmbeddingService with mocked Chroma and backends."""
        with patch("chromadb.HttpClient", return_value=mock_chroma_client):
            with patch("app.services.embedding_service.create_embeddings_backend"):
                service = EmbeddingService(
                    embedding_provider="huggingface",
                    embedding_api_key="test-key",
                    embedding_api_base_url="http://test",
                    chroma_host="localhost",
                    chroma_port=8000,
                    chroma_tenant="test_tenant",
                    embedding_dimension=768,
                )
        return service

    @pytest.mark.asyncio
    async def test_embed_query_success(self, embedding_service):
        """Test successful embedding generation."""
        # Mock the OpenAI embeddings
        test_embedding = [0.1] * 768
        embedding_service.embeddings.aembed_query = AsyncMock(
            return_value=test_embedding
        )

        result = await embedding_service.embed_query("test query")

        assert result == test_embedding
        assert len(result) == 768
        embedding_service.embeddings.aembed_query.assert_called_once_with("test query")

    @pytest.mark.asyncio
    async def test_embed_query_empty_text(self, embedding_service):
        """Test embedding with empty text raises error."""
        with pytest.raises(ValueError, match="Cannot embed empty text"):
            await embedding_service.embed_query("")

    @pytest.mark.asyncio
    async def test_embed_query_whitespace_only(self, embedding_service):
        """Test embedding with whitespace-only text raises error."""
        with pytest.raises(ValueError, match="Cannot embed empty text"):
            await embedding_service.embed_query("   ")

    @pytest.mark.asyncio
    async def test_store_session_embedding(self, embedding_service):
        """Test storing session embedding in Chroma."""
        test_embedding = [0.1] * 768
        session_id = 123
        text = "test session text"

        await embedding_service.store_session_embedding(
            session_id, test_embedding, text
        )

        # Verify Chroma upsert was called with correct data
        embedding_service.sessions_collection.upsert.assert_called_once()
        call_args = embedding_service.sessions_collection.upsert.call_args
        assert call_args.kwargs["ids"] == ["session_123"]
        assert call_args.kwargs["embeddings"] == [test_embedding]
        assert call_args.kwargs["documents"] == [text]

    @pytest.mark.asyncio
    async def test_search_similar_sessions(self, embedding_service):
        """Test searching for similar sessions."""
        test_embedding = [0.1] * 768
        mock_results = {
            "ids": [["session_1", "session_2"]],
            "distances": [[0.1, 0.3]],
            "documents": [["session 1 text", "session 2 text"]],
        }
        embedding_service.sessions_collection.query = MagicMock(
            return_value=mock_results
        )

        results = await embedding_service.search_similar_sessions(
            test_embedding, limit=2
        )

        # Verify results: (session_id, similarity_score, text)
        assert len(results) == 2
        assert results[0] == (1, 0.9, "session 1 text")  # 1 - 0.1 = 0.9 similarity
        assert results[1] == (2, 0.7, "session 2 text")  # 1 - 0.3 = 0.7 similarity

    def test_validate_embedding_text_valid(self):
        """Test validation of valid text."""
        assert EmbeddingService.validate_embedding_text("valid text") is True

    def test_validate_embedding_text_empty(self):
        """Test validation rejects empty text."""
        assert EmbeddingService.validate_embedding_text("") is False
        assert EmbeddingService.validate_embedding_text("   ") is False

    def test_validate_embedding_text_too_long(self):
        """Test validation rejects text exceeding max length."""
        long_text = "x" * 9000  # Exceeds default 8000 char limit
        assert EmbeddingService.validate_embedding_text(long_text) is False

    def test_validate_embedding_text_custom_max_length(self):
        """Test validation with custom max length."""
        text = "x" * 100
        assert EmbeddingService.validate_embedding_text(text, max_length=50) is False
        assert EmbeddingService.validate_embedding_text(text, max_length=150) is True


# ============================================================================
# EmbeddingSearchService Tests
# ============================================================================


class TestEmbeddingSearchService:
    """Test semantic search orchestration."""

    @pytest.fixture
    def mock_embedding_service(self):
        """Create mock EmbeddingService."""
        service = AsyncMock(spec=EmbeddingService)
        service.embed_query = AsyncMock()
        service.search_similar_sessions = AsyncMock()
        service.search_similar_events = AsyncMock()
        return service

    @pytest.fixture
    def mock_db_session(self):
        """Create mock database session."""
        return MagicMock(spec=Session)

    @pytest.fixture
    def search_service(self, mock_embedding_service):
        """Create EmbeddingSearchService with mocked EmbeddingService."""
        return EmbeddingSearchService(mock_embedding_service)

    @pytest.mark.asyncio
    async def test_search_sessions_success(
        self, search_service, mock_embedding_service, mock_db_session
    ):
        """Test successful session search."""
        # Setup mocks
        query_text = "machine learning"
        test_embedding = [0.1] * 768

        mock_embedding_service.embed_query.return_value = test_embedding
        mock_embedding_service.search_similar_sessions.return_value = [
            (1, 0.95, "text 1"),
            (2, 0.87, "text 2"),
        ]

        # Create mock sessions
        mock_session_1 = Mock()
        mock_session_1.id = 1
        mock_session_1.title = "ML Session"
        mock_session_1.status = SessionStatus.PUBLISHED
        mock_session_1.event_id = 100

        mock_session_2 = Mock()
        mock_session_2.id = 2
        mock_session_2.title = "Deep Learning Session"
        mock_session_2.status = SessionStatus.PUBLISHED
        mock_session_2.event_id = 100

        # Mock CRUD reads
        with patch("app.services.embedding_search_service.session_crud") as mock_crud:
            mock_crud.read.side_effect = [mock_session_1, mock_session_2]

            results = await search_service.search_sessions(
                query=query_text,
                db=mock_db_session,
                limit=10,
                event_id=100,
            )

        assert len(results) == 2
        assert results[0].id == 1
        assert results[1].id == 2
        mock_embedding_service.embed_query.assert_called_once_with(query_text)

    @pytest.mark.asyncio
    async def test_search_sessions_filters_by_status(
        self, search_service, mock_embedding_service, mock_db_session
    ):
        """Test that search only returns published sessions."""
        test_embedding = [0.1] * 768
        mock_embedding_service.embed_query.return_value = test_embedding
        mock_embedding_service.search_similar_sessions.return_value = [
            (1, 0.95, "text 1"),
            (2, 0.87, "text 2"),
        ]

        # Mock sessions with different statuses
        mock_session_1 = Mock()
        mock_session_1.id = 1
        mock_session_1.status = SessionStatus.PUBLISHED

        mock_session_2 = Mock()
        mock_session_2.id = 2
        mock_session_2.status = SessionStatus.DRAFT  # Not published

        with patch("app.services.embedding_search_service.session_crud") as mock_crud:
            mock_crud.read.side_effect = [mock_session_1, mock_session_2]

            results = await search_service.search_sessions(
                query="test",
                db=mock_db_session,
                limit=10,
            )

        # Should only return published session
        assert len(results) == 1
        assert results[0].id == 1

    @pytest.mark.asyncio
    async def test_search_sessions_filters_by_event(
        self, search_service, mock_embedding_service, mock_db_session
    ):
        """Test that search filters results by event_id."""
        test_embedding = [0.1] * 768
        mock_embedding_service.embed_query.return_value = test_embedding
        mock_embedding_service.search_similar_sessions.return_value = [
            (1, 0.95, "text 1"),
            (2, 0.87, "text 2"),
        ]

        mock_session_1 = Mock()
        mock_session_1.id = 1
        mock_session_1.status = SessionStatus.PUBLISHED
        mock_session_1.event_id = 100  # Matches filter

        mock_session_2 = Mock()
        mock_session_2.id = 2
        mock_session_2.status = SessionStatus.PUBLISHED
        mock_session_2.event_id = 999  # Different event

        with patch("app.services.embedding_search_service.session_crud") as mock_crud:
            mock_crud.read.side_effect = [mock_session_1, mock_session_2]

            results = await search_service.search_sessions(
                query="test",
                db=mock_db_session,
                limit=10,
                event_id=100,
            )

        # Should only return session from event 100
        assert len(results) == 1
        assert results[0].event_id == 100

    @pytest.mark.asyncio
    async def test_search_sessions_invalid_query(
        self, search_service, mock_embedding_service
    ):
        """Test search with invalid query raises InvalidEmbeddingTextError."""
        from app.services.embedding_exceptions import InvalidEmbeddingTextError

        with patch(
            "app.services.embedding_search_service.EmbeddingService.validate_embedding_text",
            return_value=False,
        ):
            with pytest.raises(
                InvalidEmbeddingTextError, match="Query text is invalid or too long"
            ):
                await search_service.search_sessions(
                    query="x" * 9000,  # Too long
                    db=Mock(),
                    limit=10,
                )

    @pytest.mark.asyncio
    async def test_search_events_success(
        self, search_service, mock_embedding_service, mock_db_session
    ):
        """Test successful event search."""
        test_embedding = [0.1] * 768
        mock_embedding_service.embed_query.return_value = test_embedding
        mock_embedding_service.search_similar_events.return_value = [
            (1, 0.92, "event 1 text"),
        ]

        mock_event = Mock()
        mock_event.id = 1
        mock_event.title = "Conference 2024"
        mock_event.status = EventStatus.PUBLISHED

        with patch("app.services.embedding_search_service.event_crud") as mock_crud:
            mock_crud.read.return_value = mock_event

            results = await search_service.search_events(
                query="conference",
                db=mock_db_session,
                limit=10,
            )

        assert len(results) == 1
        assert results[0].id == 1

    @pytest.mark.asyncio
    async def test_search_events_handles_chroma_errors(
        self, search_service, mock_embedding_service, mock_db_session
    ):
        """Test that Chroma errors are properly logged and propagated."""
        test_embedding = [0.1] * 768
        mock_embedding_service.embed_query.return_value = test_embedding
        mock_embedding_service.search_similar_events.side_effect = Exception(
            "Chroma connection failed"
        )

        with pytest.raises(Exception, match="Chroma connection failed"):
            await search_service.search_events(
                query="test",
                db=mock_db_session,
                limit=10,
            )


# ============================================================================
# Integration Tests
# ============================================================================


class TestEmbeddingIntegration:
    """Integration tests for embedding pipeline."""

    @pytest.mark.asyncio
    async def test_full_search_pipeline(self):
        """Test end-to-end semantic search workflow (with mocks)."""
        # This test verifies the full integration without a real database/Chroma
        query_text = "python machine learning"

        # Mock the entire pipeline
        with patch(
            "app.services.embedding_service.EmbeddingService"
        ) as MockEmbeddingService, patch(
            "app.services.embedding_search_service.session_crud"
        ) as mock_crud:

            mock_service = AsyncMock()
            mock_service.embed_query = AsyncMock(return_value=[0.1] * 768)
            mock_service.search_similar_sessions = AsyncMock(
                return_value=[(1, 0.95, "text")]
            )
            MockEmbeddingService.return_value = mock_service

            mock_session = Mock()
            mock_session.status = SessionStatus.PUBLISHED
            mock_crud.read.return_value = mock_session

            search_service = EmbeddingSearchService(mock_service)
            results = await search_service.search_sessions(
                query=query_text,
                db=Mock(),
                limit=10,
            )

            assert len(results) == 1


# ============================================================================
# Generic/Refactored Components Tests
# ============================================================================


class TestGenericEmbedding:
    """Test generic embedding infrastructure."""

    def test_prepare_embedding_text_factory_session(self):
        """Test session text preparation factory."""
        from app.async_jobs.tasks import _prepare_embedding_text_factory
        from app.constants.embedding import ENTITY_TYPE_SESSION

        mock_service = Mock()
        mock_service._prepare_session_text = Mock(return_value="prepared session text")

        mock_session = Mock()
        mock_session.id = 1
        mock_session.title = "Test Session"
        mock_session.short_description = "Short desc"
        mock_session.summary = "Full summary"

        prepare_fn = _prepare_embedding_text_factory(ENTITY_TYPE_SESSION)

        with patch("app.async_jobs.tasks.content_crud") as mock_content_crud:
            with patch("app.async_jobs.tasks.SessionLocal") as mock_session_local:
                result = prepare_fn(mock_service, mock_session)

        assert result == "prepared session text"
        mock_service._prepare_session_text.assert_called_once()

    def test_prepare_embedding_text_factory_event(self):
        """Test event text preparation factory."""
        from app.async_jobs.tasks import _prepare_embedding_text_factory
        from app.constants.embedding import ENTITY_TYPE_EVENT

        mock_service = Mock()
        mock_service._prepare_event_text = Mock(return_value="prepared event text")

        mock_event = Mock()
        mock_event.id = 1
        mock_event.title = "Test Event"
        mock_event.description = "Event description"

        prepare_fn = _prepare_embedding_text_factory(ENTITY_TYPE_EVENT)
        result = prepare_fn(mock_service, mock_event)

        assert result == "prepared event text"
        mock_service._prepare_event_text.assert_called_once()

    def test_crud_mapper_session(self):
        """Test CRUD mapper for sessions."""
        from app.async_jobs.tasks import _crud_mapper
        from app.constants.embedding import ENTITY_TYPE_SESSION

        mapper = _crud_mapper(ENTITY_TYPE_SESSION)

        assert "read" in mapper
        assert "store" in mapper
        assert mapper["entity_name"] == "session"
        assert mapper["log_prefix"] == "session"

    def test_crud_mapper_event(self):
        """Test CRUD mapper for events."""
        from app.async_jobs.tasks import _crud_mapper
        from app.constants.embedding import ENTITY_TYPE_EVENT

        mapper = _crud_mapper(ENTITY_TYPE_EVENT)

        assert "read" in mapper
        assert "store" in mapper
        assert mapper["entity_name"] == "event"
        assert mapper["log_prefix"] == "event"

    def test_generic_search_by_collection(self):
        """Test generic search_by_collection method."""
        # Create fixtures for this test
        mock_embedding_service = AsyncMock(spec=EmbeddingService)
        mock_db_session = MagicMock(spec=Session)

        mock_embedding_service.search_similar_sessions = AsyncMock(
            return_value=[(1, 0.95, "text")]
        )

        mock_entity = Mock()
        mock_entity.id = 1
        mock_entity.status = SessionStatus.PUBLISHED

        search_service = EmbeddingSearchService(mock_embedding_service)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                search_service.search_by_collection(
                    query="test",
                    db=mock_db_session,
                    search_fn=mock_embedding_service.search_similar_sessions,
                    crud_read=Mock(return_value=mock_entity),
                    status_filter=SessionStatus.PUBLISHED,
                    limit=10,
                    entity_name="session",
                )
            )
        finally:
            loop.close()

        assert len(results) == 1
        assert results[0].id == 1

    def test_text_preparation_unification(self):
        """Test that text preparation methods are unified."""
        # Create service with mocks
        with patch("chromadb.HttpClient"), patch(
            "app.services.embedding_service.create_embeddings_backend"
        ):
            service = EmbeddingService(
                embedding_provider="huggingface",
                embedding_api_key="test",
                embedding_api_base_url="http://test",
            )

        # Test generic _prepare_text method exists and works
        text = service._prepare_text(
            title="Title",
            fields=["field1", "field2", None],
        )

        assert "Title" in text
        assert "field1" in text
        assert "field2" in text

        # Test _prepare_session_text delegates to generic method
        session_text = service._prepare_session_text(
            title="Session Title",
            short_description="Short",
            summary="Long summary",
        )

        assert "Session Title" in session_text
        assert "Short" in session_text

        # Test _prepare_event_text delegates to generic method
        event_text = service._prepare_event_text(
            title="Event Title",
            description="Event desc",
        )

        assert "Event Title" in event_text
        assert "Event desc" in event_text


# ============================================================================
# Performance Tests
# ============================================================================


class TestEmbeddingPerformance:
    """Test performance characteristics."""

    def test_validate_embedding_text_performance(self):
        """Test that validation is fast even for max-length text."""
        import time

        # Create 8000 char text
        large_text = "x" * 8000

        start = time.time()
        for _ in range(1000):
            EmbeddingService.validate_embedding_text(large_text)
        elapsed = time.time() - start

        # Should be very fast (< 1ms per call)
        assert elapsed < 1.0, f"Validation too slow: {elapsed}s for 1000 calls"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
