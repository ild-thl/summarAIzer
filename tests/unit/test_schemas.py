"""Tests for schema validation."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.session import (
    EventCreate,
    EventUpdate,
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

    def test_session_create_with_speakers(self):
        """Test that speakers can be a simple list of names."""
        now = datetime.utcnow()
        session = SessionCreate(
            title="Test",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            language="en",
            uri="test-session",
            speakers=["John Doe", "Jane Smith"],  # Speakers are now simple names
        )
        assert session.speakers == ["John Doe", "Jane Smith"]

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
