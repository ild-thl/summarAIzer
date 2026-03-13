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


class TestSessionEventEmissions:
    """Test suite for session event emissions via event bus."""

    def test_event_emitted_on_create_published_session(self, test_db, sample_event):
        """Test that session_published event is emitted when creating a published session."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Published at Creation",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="pub-at-creation",
            event_id=sample_event.id,
        )

        with patch.object(SessionEventBus, "emit") as mock_emit:
            session = session_crud.create(test_db, session_create)

            # Verify event was emitted
            mock_emit.assert_called_once()
            call_args = mock_emit.call_args

            # Check event name
            assert call_args[0][0] == "session_published"

            # Check event data
            assert call_args[1]["session_id"] == session.id
            assert call_args[1]["uri"] == "pub-at-creation"
            assert call_args[1]["event_id"] == sample_event.id

    def test_event_not_emitted_on_create_draft_session(self, test_db, sample_event):
        """Test that session_published event is NOT emitted when creating a draft session."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Draft Session",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,
            language="en",
            uri="draft-session",
            event_id=sample_event.id,
        )

        with patch.object(SessionEventBus, "emit") as mock_emit:
            session = session_crud.create(test_db, session_create)

            # Verify event was NOT emitted
            mock_emit.assert_not_called()

    def test_event_emitted_on_update_draft_to_published(self, test_db, sample_event):
        """Test that event is emitted when updating draft session to published."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Initially Draft",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,
            language="en",
            uri="draft-to-pub",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)

        with patch.object(SessionEventBus, "emit") as mock_emit:
            update_data = SessionUpdate(status=SessionStatus.PUBLISHED)
            updated_session = session_crud.update(test_db, session.id, update_data)

            # Verify event WAS emitted
            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "session_published"
            assert call_args[1]["previous_status"] == SessionStatus.DRAFT

    def test_event_not_emitted_on_update_published_to_published(
        self, test_db, sample_event
    ):
        """Test that event is NOT emitted when updating published session (no status change)."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        # Create a published session
        session_create = SessionCreate(
            title="Already Published",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="already-pub",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)

        with patch.object(SessionEventBus, "emit") as mock_emit:
            # Reset mock to ignore the create call
            mock_emit.reset_mock()

            # Update other field, keep status as PUBLISHED
            update_data = SessionUpdate(title="Updated Title")
            updated_session = session_crud.update(test_db, session.id, update_data)

            # Verify event was NOT emitted (no status change)
            mock_emit.assert_not_called()

    def test_event_emitted_with_correct_metadata(self, test_db, sample_event):
        """Test that emitted events contain correct metadata."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Metadata Test",
            short_description="Testing metadata",
            start_datetime=now,
            end_datetime=now + timedelta(hours=2),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="metadata-test",
            event_id=sample_event.id,
            speakers=["Alice", "Bob"],
            categories=["Testing"],
        )

        with patch.object(SessionEventBus, "emit") as mock_emit:
            session = session_crud.create(test_db, session_create)

            # Verify event metadata
            call_args = mock_emit.call_args

            assert call_args[1]["session_id"] == session.id
            assert call_args[1]["uri"] == session.uri
            assert call_args[1]["event_id"] == sample_event.id

    def test_event_handler_called_when_event_emitted(self, test_db, sample_event):
        """Test that the embedding handler is actually called when event is emitted."""
        from unittest.mock import patch, MagicMock
        from app.events import SessionEventBus
        from app.async_jobs.tasks import generate_session_embedding

        now = datetime.utcnow()
        session_create = SessionCreate(
            title="Handler Test",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="handler-test",
            event_id=sample_event.id,
        )

        with patch.object(generate_session_embedding, "delay") as mock_delay:
            session = session_crud.create(test_db, session_create)

            # Verify the embedding task was queued
            mock_delay.assert_called_once_with(session.id)

    def test_event_emitted_on_update_published_to_draft(self, test_db, sample_event):
        """Test that session_unpublished event is emitted when updating published to draft."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        # Create a published session
        session_create = SessionCreate(
            title="Published to Unpublish",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="pub-to-draft",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)

        with patch.object(SessionEventBus, "emit") as mock_emit:
            # Reset mock to ignore the create call
            mock_emit.reset_mock()

            # Update status from PUBLISHED to DRAFT
            update_data = SessionUpdate(status=SessionStatus.DRAFT)
            updated_session = session_crud.update(test_db, session.id, update_data)

            # Verify unpublish event WAS emitted
            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "session_unpublished"
            assert call_args[1]["session_id"] == session.id
            assert call_args[1]["previous_status"] == SessionStatus.PUBLISHED

    def test_event_handler_called_on_unpublish(self, test_db, sample_event):
        """Test that deletion handler is called when session is unpublished."""
        from unittest.mock import patch, MagicMock
        from app.events import SessionEventBus
        from app.async_jobs.tasks import delete_session_embedding

        now = datetime.utcnow()
        # Create a published session
        session_create = SessionCreate(
            title="Unpublish Handler Test",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="unpub-handler-test",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)

        with patch.object(delete_session_embedding, "delay") as mock_delay:
            # Update to draft to trigger unpublish event
            update_data = SessionUpdate(status=SessionStatus.DRAFT)
            updated_session = session_crud.update(test_db, session.id, update_data)

            # Verify the deletion task was queued
            mock_delay.assert_called_once_with(session.id)

    def test_event_emitted_on_session_delete_published(self, test_db, sample_event):
        """Test that session_deleted event is emitted when deleting a published session."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        # Create a published session
        session_create = SessionCreate(
            title="Session to Delete",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="delete-test",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)
        session_id = session.id

        with patch.object(SessionEventBus, "emit") as mock_emit:
            # Reset mock to ignore the create call
            mock_emit.reset_mock()

            # Delete the session
            deleted = session_crud.delete(test_db, session_id)

            # Verify deletion succeeded
            assert deleted is True

            # Verify delete event WAS emitted
            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert call_args[0][0] == "session_deleted"
            assert call_args[1]["session_id"] == session_id
            assert call_args[1]["uri"] == "delete-test"
            assert call_args[1]["event_id"] == sample_event.id

    def test_event_not_emitted_on_delete_draft_session(self, test_db, sample_event):
        """Test that session_deleted event is NOT emitted when deleting a draft session."""
        from unittest.mock import patch
        from app.events import SessionEventBus

        now = datetime.utcnow()
        # Create a draft session
        session_create = SessionCreate(
            title="Draft Session to Delete",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,
            language="en",
            uri="delete-draft-test",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)

        with patch.object(SessionEventBus, "emit") as mock_emit:
            # Reset mock to ignore the create call
            mock_emit.reset_mock()

            # Delete the draft session
            deleted = session_crud.delete(test_db, session.id)

            # Verify deletion succeeded
            assert deleted is True

            # Verify delete event was NOT emitted (only for published sessions)
            mock_emit.assert_not_called()

    def test_event_handler_called_on_delete(self, test_db, sample_event):
        """Test that deletion handler is called when a published session is deleted."""
        from unittest.mock import patch, MagicMock
        from app.events import SessionEventBus
        from app.async_jobs.tasks import delete_session_embedding

        now = datetime.utcnow()
        # Create a published session
        session_create = SessionCreate(
            title="Delete Handler Test",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            language="en",
            uri="delete-handler-test",
            event_id=sample_event.id,
        )
        session = session_crud.create(test_db, session_create)

        with patch.object(delete_session_embedding, "delay") as mock_delay:
            # Delete the session
            deleted = session_crud.delete(test_db, session.id)

            # Verify deletion succeeded
            assert deleted is True

            # Verify the deletion task was queued
            mock_delay.assert_called_once_with(session.id)
