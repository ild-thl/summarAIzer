"""Integration tests for session documentation artifact feature."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.crud.generated_content import create_content
from app.database.models import Session as SessionModel
from app.database.models import SessionStatus
from app.schemas.session import SessionDocumentationResponse


@pytest.fixture
def session_published(test_db, sample_event, sample_user):
    """Create a published session with generated content."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Test Talk",
        uri="test-talk",
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
    create_content(
        db=test_db,
        session_id=session.id,
        identifier="key_takeaways",
        content_type="markdown",
        content="- Point 1\n- Point 2\n- Point 3",
    )

    return session


@pytest.fixture
def session_draft(test_db, sample_event, sample_user):
    """Create a draft session."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Draft Talk",
        uri="draft-talk",
        event_id=sample_event.id,
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.DRAFT,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session


class TestSessionDocumentationEndpoint:
    """Test the GET /{session_id}/documentation endpoint."""

    def test_returns_404_for_missing_session(self, client: TestClient):
        """Should return 404 when session doesn't exist."""
        response = client.get("/sessions/99999/documentation")
        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"

    def test_draft_session_not_accessible_to_anonymous(self, client: TestClient, session_draft):
        """Should return 403 when anonymous user tries to access draft session docs."""
        response = client.get(f"/sessions/{session_draft.id}/documentation")
        assert response.status_code == 403

    def test_draft_session_accessible_to_owner(
        self, client: TestClient, session_draft, user_token_header
    ):
        """Should return 404 (no artifact) when owner accesses their draft."""
        # Even though user owns it, artifact doesn't exist yet
        headers = user_token_header(session_draft.owner)
        response = client.get(f"/sessions/{session_draft.id}/documentation", headers=headers)
        assert response.status_code == 404
        assert "not yet generated" in response.json()["detail"]

    def test_published_session_without_artifact_returns_404(
        self, client: TestClient, session_published
    ):
        """Should return 404 when published session has no artifact yet."""
        # Clear the artifact that might have been built
        from app.database.connection import get_db

        db = next(get_db())

        session_published.published_documentation_artifact = None
        db.commit()

        response = client.get(f"/sessions/{session_published.id}/documentation")
        assert response.status_code == 404

    def test_published_session_with_artifact_returns_200(
        self,
        client: TestClient,
        session_published,
        db_session,
    ):
        """Should return 200 with artifact for published session."""
        from app.services.documentation_builder import DocumentationBuilder

        # Build artifact
        DocumentationBuilder.build_documentation(db_session, session_published.id)
        db_session.refresh(session_published)

        response = client.get(f"/sessions/{session_published.id}/documentation")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session_published.id
        assert data["title"] == "Test Talk"
        assert len(data["sections"]) == 2
        assert data["doc_version"] == "1.0"

    def test_artifact_contains_all_sections(
        self, client: TestClient, session_published, db_session
    ):
        """Should include all generated content sections in artifact."""
        from app.services.documentation_builder import DocumentationBuilder

        DocumentationBuilder.build_documentation(db_session, session_published.id)
        db_session.refresh(session_published)

        response = client.get(f"/sessions/{session_published.id}/documentation")
        data = response.json()

        # Check sections
        sections = data["sections"]
        identifiers = [s["identifier"] for s in sections]
        assert "summary" in identifiers
        assert "key_takeaways" in identifiers

        # Check section structure
        summary_section = next(s for s in sections if s["identifier"] == "summary")
        assert summary_section["type"] == "markdown"
        assert "Summary" in summary_section["content"]
        assert summary_section["order"] == 0

    def test_artifact_contains_session_metadata(
        self, client: TestClient, session_published, db_session
    ):
        """Should include core session metadata in artifact."""
        from app.services.documentation_builder import DocumentationBuilder

        DocumentationBuilder.build_documentation(db_session, session_published.id)
        db_session.refresh(session_published)

        response = client.get(f"/sessions/{session_published.id}/documentation")
        data = response.json()

        assert data["id"] == session_published.id
        assert data["title"] == session_published.title
        assert data["language"] == session_published.language
        assert data["uri"] == session_published.uri
        assert "generated_at" in data
        assert "updated_at" in data

    def test_artifact_keeps_only_latest_section_per_identifier(self, session_published, test_db):
        """Should keep only the newest generated content per identifier."""
        from app.services.documentation_builder import DocumentationBuilder

        # Create a newer version for an existing identifier.
        create_content(
            db=test_db,
            session_id=session_published.id,
            identifier="summary",
            content_type="markdown",
            content="# Summary\n\nThis is the latest summary.",
        )

        artifact = DocumentationBuilder.build_documentation(test_db, session_published.id)
        test_db.refresh(session_published)

        assert artifact is not None
        sections = artifact["sections"]
        summary_sections = [s for s in sections if s["identifier"] == "summary"]

        assert len(summary_sections) == 1
        assert "latest summary" in summary_sections[0]["content"]

    def test_artifact_uses_transcription_link_instead_of_inline_content(
        self, session_published, test_db
    ):
        """Should expose transcription as link metadata, not as large inline payload."""
        from app.services.documentation_builder import DocumentationBuilder

        create_content(
            db=test_db,
            session_id=session_published.id,
            identifier="transcription",
            content_type="plain_text",
            content="Very long transcript content",
        )

        artifact = DocumentationBuilder.build_documentation(test_db, session_published.id)
        test_db.refresh(session_published)

        assert artifact is not None
        sections = artifact["sections"]
        transcription_section = next(s for s in sections if s["identifier"] == "transcription")

        assert transcription_section["type"] == "resource_link"
        assert (
            transcription_section["content"]
            == f"/sessions/{session_published.id}/content/transcription"
        )


class TestSessionDocumentationByUriEndpoint:
    """Test the GET /by-uri/{uri}/documentation endpoint."""

    def test_returns_404_for_missing_uri(self, client: TestClient):
        """Should return 404 when session with URI doesn't exist."""
        response = client.get("/sessions/by-uri/nonexistent-talk/documentation")
        assert response.status_code == 404

    def test_returns_documentation_by_uri(self, client: TestClient, session_published, db_session):
        """Should return documentation artifact when accessing by URI."""
        from app.services.documentation_builder import DocumentationBuilder

        DocumentationBuilder.build_documentation(db_session, session_published.id)
        db_session.refresh(session_published)

        response = client.get(f"/sessions/by-uri/{session_published.uri}/documentation")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session_published.id
        assert data["uri"] == session_published.uri


class TestDocumentationEventHandler:
    """Test that documentation is built when session is published."""

    def test_documentation_built_on_session_publish_event(self, test_db, sample_event, sample_user):
        """Should build documentation artifact when session_published event is emitted."""
        from app.events.session_events import SessionEventBus

        now = datetime.utcnow()
        session = SessionModel(
            title="Event Talk",
            uri="event-talk",
            event_id=sample_event.id,
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.DRAFT,  # Start as draft
            owner_id=sample_user.id,
        )
        test_db.add(session)
        test_db.commit()
        test_db.refresh(session)

        # Add content
        create_content(
            db=test_db,
            session_id=session.id,
            identifier="summary",
            content_type="markdown",
            content="Auto-built summary",
        )

        # Verify no artifact yet
        test_db.refresh(session)
        assert session.published_documentation_artifact is None

        # Publish session (triggers event)
        session.status = SessionStatus.PUBLISHED
        test_db.commit()
        SessionEventBus.emit("session_published", session_id=session.id)

        # Verify artifact was built
        test_db.refresh(session)
        assert session.published_documentation_artifact is not None
        artifact = session.published_documentation_artifact
        assert artifact["id"] == session.id
        assert len(artifact["sections"]) == 1
        assert artifact["sections"][0]["identifier"] == "summary"

    def test_documentation_includes_optional_sections(self, test_db, sample_event, sample_user):
        """Documentation should work with varying content availability."""
        from app.services.documentation_builder import DocumentationBuilder

        now = datetime.utcnow()
        session = SessionModel(
            title="Minimal Talk",
            uri="minimal-talk",
            event_id=sample_event.id,
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status=SessionStatus.PUBLISHED,
            owner_id=sample_user.id,
        )
        test_db.add(session)
        test_db.commit()
        test_db.refresh(session)

        # Add only one content type
        create_content(
            db=test_db,
            session_id=session.id,
            identifier="summary",
            content_type="markdown",
            content="Only summary",
        )

        # Build documentation
        artifact = DocumentationBuilder.build_documentation(test_db, session.id)

        # Verify artifact has only one section
        assert artifact is not None
        assert len(artifact["sections"]) == 1
        assert artifact["sections"][0]["identifier"] == "summary"


class TestDocumentationResponseSchema:
    """Test the SessionDocumentationResponse schema."""

    def test_response_deserializes_from_artifact(self, session_published, db_session):
        """Should deserialize stored artifact into response model."""
        from app.services.documentation_builder import DocumentationBuilder

        DocumentationBuilder.build_documentation(db_session, session_published.id)
        db_session.refresh(session_published)

        artifact = session_published.published_documentation_artifact
        response = SessionDocumentationResponse(**artifact)

        assert response.id == session_published.id
        assert response.title == session_published.title
        assert len(response.sections) == 2
        assert response.doc_version == "1.0"
