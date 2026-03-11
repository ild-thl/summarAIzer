"""Tests for Session CRUD operations."""

import pytest
from datetime import datetime, timedelta
from app.crud.session import session_crud
from app.schemas.session import SessionCreate, SessionUpdate
from app.database.models import SessionStatus, SessionFormat


class TestSessionCRUD:
    """Test suite for Session CRUD operations."""

    def test_create_session_success(self, test_db, sample_event):
        """Test creating a session successfully."""
        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Test Session",
            short_description="Test description",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            location="Room 101",
            status=SessionStatus.DRAFT,
            session_format=SessionFormat.WORKSHOP,
            language="en",
            uri="test-session",
            event_id=sample_event.id,
            speakers=["John Doe", "Jane Smith"],
            categories=["AI", "Testing"],
            duration=60,
        )

        session = session_crud.create(test_db, session_create)

        assert session.id is not None
        assert session.title == "Test Session"
        assert session.uri == "test-session"
        assert session.status == SessionStatus.DRAFT
        assert session.event_id == sample_event.id

    def test_create_session_standalone(self, test_db):
        """Test creating a session without an event."""
        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Standalone Session",
            start_datetime=now,
            end_datetime=now + timedelta(hours=2),
            language="en",
            uri="standalone-session",
        )

        session = session_crud.create(test_db, session_create)

        assert session.id is not None
        assert session.event_id is None

    def test_read_session_by_id(self, test_db, sample_session):
        """Test reading a session by ID."""
        session = session_crud.read(test_db, sample_session.id)

        assert session is not None
        assert session.id == sample_session.id
        assert session.title == sample_session.title

    def test_read_session_by_uri(self, test_db, sample_session):
        """Test reading a session by URI."""
        session = session_crud.read_by_uri(test_db, sample_session.uri)

        assert session is not None
        assert session.uri == sample_session.uri

    def test_list_all_sessions(self, test_db, sample_session):
        """Test listing all sessions."""
        sessions = session_crud.list_all(test_db)

        assert len(sessions) >= 1
        assert sample_session.id in [s.id for s in sessions]

    def test_list_sessions_by_event(self, test_db, sample_session, sample_event):
        """Test listing sessions by event."""
        sessions = session_crud.list_by_event(test_db, sample_event.id)

        assert len(sessions) >= 1
        assert sample_session.id in [s.id for s in sessions]

    def test_list_sessions_by_event_empty(self, test_db):
        """Test listing sessions for an event with no sessions."""
        sessions = session_crud.list_by_event(test_db, 999)

        assert len(sessions) == 0

    def test_list_sessions_by_status(self, test_db):
        """Test listing sessions filtered by status."""
        now = datetime.utcnow()

        # Create draft session
        draft_session = SessionCreate(
            title="Draft Session",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,
            language="en",
            uri="draft-session",
        )
        session_crud.create(test_db, draft_session)

        # Create published session
        published_session = SessionCreate(
            title="Published Session",
            start_datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=1, hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="published-session",
        )
        session_crud.create(test_db, published_session)

        # List by status
        draft_sessions = session_crud.list_by_status(test_db, SessionStatus.DRAFT)
        published_sessions = session_crud.list_by_status(
            test_db, SessionStatus.PUBLISHED
        )

        assert len(draft_sessions) >= 1
        assert len(published_sessions) >= 1

    def test_list_published_sessions(self, test_db):
        """Test listing only published sessions."""
        now = datetime.utcnow()

        # Create a draft session
        draft = SessionCreate(
            title="Draft",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,
            language="en",
            uri="draft-1",
        )
        session_crud.create(test_db, draft)

        # Create a published session
        published = SessionCreate(
            title="Published",
            start_datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=1, hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="published-1",
        )
        session_crud.create(test_db, published)

        # List published only
        published_sessions = session_crud.list_published(test_db)

        assert len(published_sessions) >= 1
        assert all(s.status == SessionStatus.PUBLISHED for s in published_sessions)

    def test_update_session(self, test_db, sample_session):
        """Test updating a session."""
        update_data = SessionUpdate(
            title="Updated Session",
            status=SessionStatus.PUBLISHED,
        )

        updated_session = session_crud.update(test_db, sample_session.id, update_data)

        assert updated_session is not None
        assert updated_session.title == "Updated Session"
        assert updated_session.status == SessionStatus.PUBLISHED

    def test_delete_session(self, test_db, sample_session):
        """Test deleting a session."""
        session_id = sample_session.id

        result = session_crud.delete(test_db, session_id)

        assert result is True
        assert session_crud.read(test_db, session_id) is None

    def test_count_sessions(self, test_db, sample_session):
        """Test counting sessions."""
        count = session_crud.count(test_db)

        assert count >= 1

    def test_count_sessions_by_event(self, test_db, sample_event, sample_session):
        """Test counting sessions in an event."""
        count = session_crud.count_by_event(test_db, sample_event.id)

        assert count >= 1
