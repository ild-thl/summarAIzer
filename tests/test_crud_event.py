"""Tests for Event CRUD operations."""

import pytest
from datetime import datetime, timedelta
from app.crud.event import event_crud
from app.schemas.session import EventCreate, EventUpdate
from app.database.models import EventStatus


class TestEventCRUD:
    """Test suite for Event CRUD operations."""

    def test_create_event_success(self, test_db):
        """Test creating an event successfully."""
        now = datetime.utcnow()
        event_create = EventCreate(
            title="Test Event",
            description="Test description",
            start_date=now,
            end_date=now + timedelta(days=1),
            location="Test Location",
            status=EventStatus.DRAFT,
            uri="test-event",
        )

        event = event_crud.create(test_db, event_create)

        assert event.id is not None
        assert event.title == "Test Event"
        assert event.uri == "test-event"
        assert event.status == EventStatus.DRAFT

    def test_create_event_with_minimal_fields(self, test_db):
        """Test creating an event with minimal required fields."""
        now = datetime.utcnow()
        event_create = EventCreate(
            title="Minimal Event",
            start_date=now,
            end_date=now + timedelta(hours=2),
            uri="minimal-event",
        )

        event = event_crud.create(test_db, event_create)

        assert event.title == "Minimal Event"
        assert event.description is None
        assert event.location is None

    def test_read_event_by_id(self, test_db, sample_event):
        """Test reading an event by ID."""
        event = event_crud.read(test_db, sample_event.id)

        assert event is not None
        assert event.id == sample_event.id
        assert event.title == sample_event.title

    def test_read_event_by_id_not_found(self, test_db):
        """Test reading a non-existent event."""
        event = event_crud.read(test_db, 999)

        assert event is None

    def test_read_event_by_uri(self, test_db, sample_event):
        """Test reading an event by URI."""
        event = event_crud.read_by_uri(test_db, sample_event.uri)

        assert event is not None
        assert event.uri == sample_event.uri

    def test_read_event_by_uri_not_found(self, test_db):
        """Test reading a non-existent event by URI."""
        event = event_crud.read_by_uri(test_db, "non-existent-uri")

        assert event is None

    def test_list_all_events(self, test_db, sample_event):
        """Test listing all events."""
        events = event_crud.list_all(test_db)

        assert len(events) >= 1
        assert sample_event.id in [e.id for e in events]

    def test_list_events_with_pagination(self, test_db):
        """Test listing events with pagination."""
        now = datetime.utcnow()
        # Create multiple events
        for i in range(5):
            event_create = EventCreate(
                title=f"Event {i}",
                start_date=now,
                end_date=now + timedelta(days=1),
                uri=f"event-{i}",
            )
            event_crud.create(test_db, event_create)

        # Test pagination
        events_page1 = event_crud.list_all(test_db, skip=0, limit=2)
        events_page2 = event_crud.list_all(test_db, skip=2, limit=2)

        assert len(events_page1) == 2
        assert len(events_page2) == 2
        assert events_page1[0].id != events_page2[0].id

    def test_list_events_by_status(self, test_db):
        """Test listing events filtered by status."""
        now = datetime.utcnow()

        # Create draft event
        draft_event = EventCreate(
            title="Draft Event",
            start_date=now,
            end_date=now + timedelta(days=1),
            status=EventStatus.DRAFT,
            uri="draft-event",
        )
        event_crud.create(test_db, draft_event)

        # Create published event
        published_event = EventCreate(
            title="Published Event",
            start_date=now,
            end_date=now + timedelta(days=1),
            status=EventStatus.PUBLISHED,
            uri="published-event",
        )
        event_crud.create(test_db, published_event)

        # List by status
        draft_events = event_crud.list_by_status(test_db, EventStatus.DRAFT)
        published_events = event_crud.list_by_status(test_db, EventStatus.PUBLISHED)

        assert len(draft_events) >= 1
        assert len(published_events) >= 1

    def test_update_event(self, test_db, sample_event):
        """Test updating an event."""
        update_data = EventUpdate(
            title="Updated Event",
            status=EventStatus.PUBLISHED,
        )

        updated_event = event_crud.update(test_db, sample_event.id, update_data)

        assert updated_event is not None
        assert updated_event.title == "Updated Event"
        assert updated_event.status == EventStatus.PUBLISHED
        assert updated_event.description == sample_event.description  # Unchanged

    def test_update_event_not_found(self, test_db):
        """Test updating a non-existent event."""
        update_data = EventUpdate(title="Updated")

        updated_event = event_crud.update(test_db, 999, update_data)

        assert updated_event is None

    def test_delete_event(self, test_db, sample_event):
        """Test deleting an event."""
        event_id = sample_event.id

        result = event_crud.delete(test_db, event_id)

        assert result is True
        assert event_crud.read(test_db, event_id) is None

    def test_delete_event_not_found(self, test_db):
        """Test deleting a non-existent event."""
        result = event_crud.delete(test_db, 999)

        assert result is False

    def test_count_events(self, test_db, sample_event):
        """Test counting events."""
        count = event_crud.count(test_db)

        assert count >= 1
