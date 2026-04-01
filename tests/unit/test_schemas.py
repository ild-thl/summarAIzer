"""Tests for schema validation."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.session import (
    EventCreate,
    SearchIntentRefinementLLMResponse,
    SearchIntentRefinementRequest,
    SessionCreate,
    SessionUpdate,
)


class TestEventSchema:
    """Test suite for Event schema validation."""

    def test_event_create_valid(self):
        """Test creating a valid Event schema."""
        now = datetime.utcnow()
        event = EventCreate(
            title="Test Event",
            start_date=now,
            end_date=now + timedelta(days=1),
            uri="test-event",
        )

        assert event.title == "Test Event"
        assert event.uri == "test-event"

    def test_event_create_uri_lower(self):
        """Test that URI is converted to lowercase."""
        now = datetime.utcnow()
        event = EventCreate(
            title="Test",
            start_date=now,
            end_date=now + timedelta(days=1),
            uri="TEST-EVENT",
        )

        assert event.uri == "test-event"

    def test_event_create_invalid_uri(self):
        """Test that invalid URI raises error."""
        now = datetime.utcnow()
        with pytest.raises(ValidationError):
            EventCreate(
                title="Test",
                start_date=now,
                end_date=now + timedelta(days=1),
                uri="test event@",  # Invalid characters
            )

    def test_event_create_end_before_start(self):
        """Test that end_date before start_date raises error."""
        now = datetime.utcnow()
        with pytest.raises(ValidationError):
            EventCreate(
                title="Test",
                start_date=now,
                end_date=now - timedelta(days=1),  # Before start
                uri="test-event",
            )

    def test_event_create_title_required(self):
        """Test that title is required."""
        now = datetime.utcnow()
        with pytest.raises(ValidationError):
            EventCreate(
                start_date=now,
                end_date=now + timedelta(days=1),
                uri="test-event",
            )


class TestSessionSchema:
    """Test suite for Session schema validation."""

    def test_session_create_valid(self):
        """Test creating a valid Session schema."""
        now = datetime.utcnow()
        session = SessionCreate(
            title="Test Session",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            language="en",
            uri="test-session",
        )

        assert session.title == "Test Session"
        assert session.uri == "test-session"

    def test_session_create_with_speakers(self):
        """Test creating a session with speakers."""
        now = datetime.utcnow()
        session = SessionCreate(
            title="Test Session",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            language="en",
            uri="test-session",
            speakers=[
                "John Doe",
                "Jane Doe",
            ],
        )

        assert len(session.speakers) == 2

    def test_session_create_end_before_start(self):
        """Test that end_datetime before start_datetime raises error."""
        now = datetime.utcnow()
        with pytest.raises(ValidationError):
            SessionCreate(
                title="Test",
                start_datetime=now,
                end_datetime=now - timedelta(hours=1),
                language="en",
                uri="test-session",
            )

    def test_session_create_title_required(self):
        """Test that title is required."""
        now = datetime.utcnow()
        with pytest.raises(ValidationError):
            SessionCreate(
                start_datetime=now,
                end_datetime=now + timedelta(hours=1),
                language="en",
                uri="test-session",
            )

    def test_session_update_partial(self):
        """Test that SessionUpdate allows partial updates."""
        update = SessionUpdate(
            title="Updated",
            status="published",
        )

        # Only these fields should be set
        assert update.title == "Updated"
        assert update.status == "published"
        assert update.start_datetime is None
        assert update.location is None


class TestSearchIntentRefinementSchemas:
    """Test suite for query refinement schema validation."""

    def test_refinement_request_normalizes_filters(self):
        """Test request schema normalization for existing filters."""
        request = SearchIntentRefinementRequest(
            queries=["  Ich will mit anderen ueber KI diskutieren  "],
            event_id=4,
            session_format=["Workshop", "diskussion", "Workshop"],
            tags=[" AI ", "Ethik", "AI"],
        )

        assert request.queries == ["Ich will mit anderen ueber KI diskutieren"]
        assert request.event_id == 4
        assert request.session_format == ["workshop", "diskussion"]
        assert request.tags == ["AI", "Ethik"]

    def test_refinement_llm_response_rejects_invalid_session_format(self):
        """Test LLM response schema validates recommended session formats."""
        with pytest.raises(ValidationError):
            SearchIntentRefinementLLMResponse(
                refined_queries=["ethischer Einsatz von KI im Unterricht"],
                recommended_session_format=["panel"],
                recommended_tags=["Ethik"],
                recommended_location=[],
                rationale="Discussion intent implies a format preference.",
            )

    def test_refinement_request_requires_event_id(self):
        """Test refinement request requires event_id."""
        with pytest.raises(ValidationError):
            SearchIntentRefinementRequest(queries=["Ich will ueber KI diskutieren"])
