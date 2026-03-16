"""Tests for content management and workflow endpoints."""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK, HTTP_201_CREATED

from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from app.database.models import GeneratedContent, WorkflowExecution


@pytest.fixture
def session_with_event(client: TestClient, test_db: Session, sample_event, sample_api_key):
    """Create a session with transcription for testing."""
    api_key, plain_key = sample_api_key
    response = client.post(
        "/api/v2/sessions",
        headers={"Authorization": f"Bearer {plain_key}"},
        json={
            "title": "AI Workshop",
            "uri": "ai-workshop",
            "start_datetime": "2026-03-10T10:00:00",
            "end_datetime": "2026-03-10T11:30:00",
            "event_id": sample_event.id,
            "speakers": ["Dr. Jane Doe"],
            "categories": ["AI", "Machine Learning"],
        },
    )
    assert response.status_code == 201
    return (
        response.json(),
        plain_key,
    )  # Return both session data and the API key for later tests


@pytest.mark.integration
class TestContentEndpoints:
    """Test content management endpoints."""

    def test_add_transcription(self, client: TestClient, session_with_event):
        """Test adding transcription to a session."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        response = client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "content": "This is a test transcription with important information about AI.",
                "content_type": "plain_text",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["identifier"] == "transcription"
        assert data["content_type"] == "plain_text"
        assert data["workflow_execution_id"] is None
        assert "AI" in data["content"]

    def test_get_available_content(self, client: TestClient, session_with_event, test_db: Session):
        """Test retrieving available content identifiers."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        # Add transcription
        client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "Test transcription"},
        )

        # Get available content (authenticated as owner)
        response = client.get(
            f"/api/v2/sessions/{session_id}/content",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "transcription" in data["available_content"]

    def test_get_content_by_identifier(self, client: TestClient, session_with_event):
        """Test retrieving content by identifier."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]
        content_text = "Test transcription content"

        # Add transcription
        client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": content_text},
        )

        # Get transcription (authenticated as owner)
        response = client.get(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["identifier"] == "transcription"
        assert data["content"] == content_text

    def test_transcription_conflict(self, client: TestClient, session_with_event):
        """Test adding duplicate transcription fails."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        # Add first transcription
        response1 = client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "First transcription"},
        )
        assert response1.status_code == 201

        # Try to add second transcription
        response2 = client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "Second transcription"},
        )
        assert response2.status_code == 409

    def test_update_content(self, client: TestClient, session_with_event):
        """Test updating generated content."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]
        original_content = "Original content"
        updated_content = "Updated content after manual edit"

        # Add transcription
        client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": original_content},
        )

        # Update content
        response = client.patch(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": updated_content},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == updated_content

    def test_delete_content(self, client: TestClient, session_with_event, test_db: Session):
        """Test deleting content."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        # Add transcription
        client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "Test content"},
        )

        # Verify content exists (authenticated)
        response = client.get(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert response.status_code == 200

        # Delete content
        response = client.delete(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert response.status_code == 204

        # Verify content is gone (authenticated)
        response = client.get(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert response.status_code == 404


@pytest.mark.integration
class TestWorkflowEndpoints:
    """Test workflow execution endpoints."""

    def test_trigger_workflow_without_transcription(self, client: TestClient, session_with_event):
        """Test that workflow requires transcription."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        response = client.post(
            f"/api/v2/sessions/{session_id}/workflow/talk_workflow",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 400
        assert "transcription" in response.json()["detail"].lower()

    def test_trigger_workflow_with_transcription(self, client: TestClient, session_with_event):
        """Test triggering workflow with transcription present."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        # Add transcription
        client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "Sample transcription for testing workflows."},
        )

        # Trigger workflow
        response = client.post(
            f"/api/v2/sessions/{session_id}/workflow/talk_workflow",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 202
        data = response.json()
        assert "task_id" in data
        assert data["workflow_type"] == "talk_workflow"
        assert data["status"] == "queued"

    def test_get_workflow_status_not_found(self, client: TestClient, session_with_event):
        """Test getting status of non-existent workflow."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        response = client.get(
            f"/api/v2/sessions/{session_id}/workflow/99999",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 404

    def test_trigger_unknown_workflow(self, client: TestClient, session_with_event):
        """Test triggering unknown workflow type."""
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        # Add transcription
        client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "Sample transcription"},
        )

        # Trigger unknown workflow
        response = client.post(
            f"/api/v2/sessions/{session_id}/workflow/unknown_workflow",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 400
        assert "unknown" in response.json()["detail"].lower()

    def test_workflow_with_available_content_tracking(self, client: TestClient, session_with_event):
        """Test that workflow execution properly tracks available_content_identifiers in DB.

        This is an integration test that verifies the database schema includes
        available_content_identifiers column and workflow execution can properly
        add content to that list.

        Regression test for: psycopg2.errors.UndefinedColumn: column sessions.available_content_identifiers does not exist
        """
        session_data, plain_key = session_with_event
        session_id = session_data["id"]

        # Add transcription as content
        response = client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": "Sample transcription for workflow test"},
        )
        assert response.status_code == HTTP_201_CREATED

        # Verify available content includes transcription (authenticated)
        content_lists = client.get(
            f"/api/v2/sessions/{session_id}/content",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert content_lists.status_code == HTTP_200_OK
        available = content_lists.json().get("available_content", [])
        assert "transcription" in available

        # Trigger workflow - this will query available_content_identifiers
        response = client.post(
            f"/api/v2/sessions/{session_id}/workflow/talk_workflow",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        # Should get valid response (202 Accepted, workflow queued)
        assert response.status_code == 202
        data = response.json()
        assert "task_id" in data
        assert data["workflow_type"] == "talk_workflow"
        assert data["status"] == "queued"


@pytest.mark.integration
class TestTranscriptionStorage:
    """Test suite for transcription storage."""

    def test_session_stores_transcription(self, client: TestClient, sample_api_key):
        """Test that transcription can be stored for a session via content endpoint."""
        api_key, plain_key = sample_api_key

        # Create event
        now = datetime.utcnow()
        event_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Transcription Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "transcription-event",
            },
        )
        event_id = event_resp.json()["id"]

        # Create session (without transcription in request - now done separately via content endpoint)
        transcription = "This is a test transcription. " * 100  # ~3KB
        response = client.post(
            f"/api/v2/events/{event_id}/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Session with Transcription",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "transcription-session",
            },
        )
        assert response.status_code == HTTP_201_CREATED
        session_id = response.json()["id"]

        # Add transcription as content
        content_resp = client.post(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"content": transcription},
        )
        assert content_resp.status_code == HTTP_201_CREATED
        assert content_resp.json()["identifier"] == "transcription"

        # Retrieve and verify transcription is stored
        detail_resp = client.get(
            f"/api/v2/sessions/{session_id}/content/transcription",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert detail_resp.status_code == HTTP_200_OK
        assert detail_resp.json()["content"] == transcription
