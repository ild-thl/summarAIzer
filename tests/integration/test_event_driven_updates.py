"""Integration tests for event-driven session updates."""

from datetime import datetime, timedelta

import pytest

from app.crud.generated_content import create_content
from app.crud.session import session_crud
from app.database.models import Session as SessionModel
from app.database.models import SessionStatus
from app.events.session_events import SessionEventBus
from app.schemas.session import SessionUpdate


@pytest.fixture
def session_published(test_db, sample_event, sample_user):
    """Create a published session with generated content."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Test Talk",
        uri="test-talk-update",
        event_id=sample_event.id,
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.PUBLISHED,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)

    # Add generated content
    create_content(
        db=test_db,
        session_id=session.id,
        identifier="summary",
        content_type="markdown",
        content="# Summary\n\nThis is a test summary.",
    )

    return session


class TestEventDrivenUpdates:
    """Test event-driven update behavior with selective embedding refresh."""

    def test_session_updated_event_always_emitted_on_update(
        self, test_db, session_published, monkeypatch
    ):
        """Should always emit session_updated event when session is updated."""
        # Track emitted events
        emitted_events = []
        original_emit = SessionEventBus.emit

        def mock_emit(event_name, **data):
            emitted_events.append((event_name, data))
            original_emit(event_name, **data)

        monkeypatch.setattr(SessionEventBus, "emit", mock_emit)

        # Update non-embedding field (like location)
        update_data = SessionUpdate(location={"name": "Room A", "city": "Berlin"})
        session_crud.update(test_db, session_published.id, update_data)

        # Should have emitted session_updated event
        updated_events = [e for e in emitted_events if e[0] == "session_updated"]
        assert len(updated_events) == 1
        assert updated_events[0][1]["session_id"] == session_published.id
        assert "changed_fields" in updated_events[0][1]

    def test_multiple_field_updates_track_all_changes(
        self, test_db, session_published, monkeypatch
    ):
        """Should track all changed fields in session_updated event."""
        from app.crud.session import session_crud

        # Track emitted events
        emitted_events = []
        original_emit = SessionEventBus.emit

        def mock_emit(event_name, **data):
            emitted_events.append((event_name, data))
            original_emit(event_name, **data)

        monkeypatch.setattr(SessionEventBus, "emit", mock_emit)

        # Mock session_crud.read to return the session from test_db
        def mock_read(db, session_id):
            return test_db.query(session_published.__class__).filter_by(id=session_id).first()

        monkeypatch.setattr(session_crud, "read", mock_read)

        # Update multiple fields
        update_data = SessionUpdate(
            title="New Title", description="New Description", tags=["tag1", "tag2"]
        )
        session_crud.update(test_db, session_published.id, update_data)

        # Check changed_fields includes all updated fields
        updated_events = [e for e in emitted_events if e[0] == "session_updated"]
        assert len(updated_events) == 1
        changed_fields = updated_events[0][1].get("changed_fields", [])
        assert "title" in changed_fields
        assert "description" in changed_fields
        assert "tags" in changed_fields

    def test_no_events_on_update_to_draft_session(
        self, test_db, sample_event, sample_user, monkeypatch
    ):
        """Draft sessions should still emit session_updated but not rebuild docs."""
        # Create a draft session
        now = datetime.utcnow()
        draft_session = SessionModel(
            title="Draft Talk",
            uri="draft-talk",
            event_id=sample_event.id,
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,
            owner_id=sample_user.id,
        )
        test_db.add(draft_session)
        test_db.commit()
        test_db.refresh(draft_session)

        # Track documentation builds
        build_calls = []

        from app.services.documentation_builder import DocumentationBuilder

        original_build = DocumentationBuilder.build_documentation

        def mock_build(db, session_id):
            build_calls.append(session_id)
            return original_build(db, session_id)

        monkeypatch.setattr(DocumentationBuilder, "build_documentation", mock_build)

        # Mock session_crud.read to return the session from test_db
        def mock_read(db, session_id):
            return test_db.query(SessionModel).filter_by(id=session_id).first()

        monkeypatch.setattr(session_crud, "read", mock_read)

        # Update draft session
        update_data = SessionUpdate(title="Updated Draft Title")
        session_crud.update(test_db, draft_session.id, update_data)

        # session_updated event should still be emitted, but NO documentation rebuild
        # (because session is not published)
        assert draft_session.id not in build_calls
