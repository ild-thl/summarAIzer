"""Tests for Session API endpoints."""

import hashlib
from datetime import datetime, timedelta

import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)


def _hash_api_key(key: str) -> str:
    """Hash API key for testing."""
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.integration
class TestSessionAPI:
    """Test suite for Session API endpoints."""

    def test_create_session_endpoint(self, client, sample_event, sample_api_key):
        """Test creating a session via API."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "API Test Session",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "short_description": "Test via API",
                "location": "Room 101",
                "language": "en",
                "uri": "api-test-session",
                "event_id": sample_event.id,
                "status": "draft",
                "speakers": ["Test Speaker", "Another Speaker"],
                "tags": ["Testing", "API"],
            },
        )

        assert response.status_code == HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "API Test Session"
        assert data["uri"] == "api-test-session"
        assert data["id"] is not None
        assert data["event_id"] == sample_event.id

    def test_create_session_without_event(self, client, sample_api_key):
        """Test creating a standalone session."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Standalone Session",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=2)).isoformat(),
                "language": "en",
                "uri": "standalone-session-api",
                "status": "published",
            },
        )

        assert response.status_code == HTTP_201_CREATED
        data = response.json()
        assert data["event_id"] is None

    def test_create_session_invalid_event_id(self, client, sample_api_key):
        """Test creating a session with non-existent event."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Invalid Event Session",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "language": "en",
                "uri": "invalid-event-session",
                "event_id": 999,  # Non-existent
            },
        )

        assert response.status_code == HTTP_404_NOT_FOUND

    def test_create_session_duplicate_uri(self, client, sample_session, sample_api_key):
        """Test creating a session with duplicate URI."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Another Session",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "language": "en",
                "uri": sample_session.uri,  # Duplicate URI
            },
        )

        assert response.status_code == HTTP_409_CONFLICT

    def test_get_session_by_id(self, client, sample_api_key, session_with_owner):
        """Test getting a session by ID (authenticated)."""
        api_key, plain_key = sample_api_key
        response = client.get(
            f"/api/v2/sessions/{session_with_owner.id}",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["id"] == session_with_owner.id
        assert data["title"] == session_with_owner.title

    def test_get_session_by_uri(self, client, sample_api_key, session_with_owner):
        """Test getting a session by URI (authenticated)."""
        api_key, plain_key = sample_api_key
        response = client.get(
            f"/api/v2/sessions/by-uri/{session_with_owner.uri}",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["uri"] == session_with_owner.uri

    def test_list_sessions(self, client, published_session):
        """Test listing sessions (public access to published)."""
        response = client.get("/api/v2/sessions")

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        # Should include the published session
        assert any(s["id"] == published_session.id for s in data)

    def test_list_sessions_by_event(self, client, sample_event, published_session):
        """Test listing sessions for a specific event (public access)."""
        response = client.get(f"/api/v2/sessions/event/{sample_event.id}/sessions")

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        # Should include the published session
        assert any(s["id"] == published_session.id for s in data)

    def test_list_sessions_published_only(self, client, sample_session_no_event):
        """Test listing only published sessions using status filter."""
        response = client.get("/api/v2/sessions?status=published")

        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Should include the published session
        assert any(s["id"] == sample_session_no_event.id for s in data)

    @pytest.mark.usefixtures("sample_session")
    def test_list_sessions_by_status(self, client, sample_api_key):
        """Test listing sessions filtered by status (authenticated)."""
        api_key, plain_key = sample_api_key
        response = client.get(
            "/api/v2/sessions?status=draft",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1

    def test_update_session(self, client, sample_session, sample_api_key):
        """Test updating a session."""
        api_key, plain_key = sample_api_key
        response = client.patch(
            f"/api/v2/sessions/{sample_session.id}",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Updated Session Title",
                "status": "published",
            },
        )

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["title"] == "Updated Session Title"
        assert data["status"] == "published"

    def test_update_session_change_event(self, client, sample_api_key, session_with_owner):
        """Test changing a session's event."""
        api_key, plain_key = sample_api_key
        # Create another event
        now = datetime.utcnow()
        event_response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Second Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "second-event",
            },
        )
        second_event_id = event_response.json()["id"]

        # Update session to different event
        response = client.patch(
            f"/api/v2/sessions/{session_with_owner.id}",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"event_id": second_event_id},
        )

        assert response.status_code == HTTP_200_OK
        assert response.json()["event_id"] == second_event_id

    def test_delete_session(self, client, sample_session, sample_api_key):
        """Test deleting a session."""
        api_key, plain_key = sample_api_key
        session_id = sample_session.id
        response = client.delete(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_204_NO_CONTENT

        # Verify deletion
        get_response = client.get(f"/api/v2/sessions/{session_id}")
        assert get_response.status_code == HTTP_404_NOT_FOUND

    def test_delete_session_not_found(self, client, sample_api_key):
        """Test deleting a non-existent session."""
        api_key, plain_key = sample_api_key
        response = client.delete(
            "/api/v2/sessions/999",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_404_NOT_FOUND


class TestSessionURIUniquenessPerEvent:
    """Test suite for per-event URI uniqueness."""

    def test_session_uri_unique_globally_fails(self, client, sample_api_key):
        """Test that global URI uniqueness is removed (should allow per-event reuse)."""
        api_key, plain_key = sample_api_key

        # Create first event and session
        now = datetime.utcnow()
        event1_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Event 1",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "event-1",
            },
        )
        event1_id = event1_resp.json()["id"]

        session1_resp = client.post(
            f"/api/v2/events/{event1_id}/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Session Title",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "shared-session-uri",
            },
        )
        assert session1_resp.status_code == HTTP_201_CREATED

        # Create second event
        event2_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Event 2",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "event-2",
            },
        )
        event2_id = event2_resp.json()["id"]

        # Should be able to create session with same URI in different event
        session2_resp = client.post(
            f"/api/v2/events/{event2_id}/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Session Title",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "shared-session-uri",  # Same URI, different event
            },
        )
        assert session2_resp.status_code == HTTP_201_CREATED

    def test_session_uri_unique_within_event(self, client, sample_api_key):
        """Test that session URIs must be unique within an event."""
        api_key, plain_key = sample_api_key

        # Create event
        now = datetime.utcnow()
        event_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "unique-test-event",
            },
        )
        event_id = event_resp.json()["id"]

        # Create first session
        session1_resp = client.post(
            f"/api/v2/events/{event_id}/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Session 1",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "duplicate-uri",
            },
        )
        assert session1_resp.status_code == HTTP_201_CREATED

        # Try to create another session with same URI in same event
        session2_resp = client.post(
            f"/api/v2/events/{event_id}/sessions",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Session 2",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "duplicate-uri",  # Duplicate in same event
            },
        )
        assert session2_resp.status_code == HTTP_409_CONFLICT


class TestSessionUpsertEndpoint:
    """Test suite for session upsert (sync) endpoint."""

    def test_upsert_session_creates_new(self, client, sample_api_key):
        """Test upsert endpoint creates new session."""
        api_key, plain_key = sample_api_key

        # Create event
        now = datetime.utcnow()
        event_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Upsert Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "upsert-event",
            },
        )
        event_id = event_resp.json()["id"]

        # Upsert: create new session
        response = client.post(
            f"/api/v2/events/{event_id}/sessions/sync",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Synced Session",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "synced-uri",
            },
        )
        assert response.status_code == HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "Synced Session"
        session_id = data["id"]

        # Verify it was created
        get_resp = client.get(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert get_resp.status_code == HTTP_200_OK

    def test_upsert_session_updates_existing(self, client, sample_api_key):
        """Test upsert endpoint updates existing session."""
        api_key, plain_key = sample_api_key

        # Create event
        now = datetime.utcnow()
        event_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Upsert Update Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "upsert-update-event",
            },
        )
        event_id = event_resp.json()["id"]

        uri = "update-uri"

        # Create initial session
        create_resp = client.post(
            f"/api/v2/events/{event_id}/sessions/sync",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Original Title",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": uri,
            },
        )
        assert create_resp.status_code == HTTP_201_CREATED
        original_id = create_resp.json()["id"]

        # Upsert: update existing session
        # Note: FastAPI upsert endpoints typically return 201 (Accepted/Created) for both create and update
        update_resp = client.post(
            f"/api/v2/events/{event_id}/sessions/sync",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Updated Title",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=2)).isoformat(),
                "uri": uri,  # Same URI
            },
        )
        # Upsert endpoints return 201 for both create and update (resource was processed)
        assert update_resp.status_code == HTTP_201_CREATED
        updated_data = update_resp.json()
        assert updated_data["id"] == original_id  # Same session
        assert updated_data["title"] == "Updated Title"

    def test_upsert_requires_event_ownership(self, test_db, client, sample_api_key):
        """Test upsert requires event ownership."""
        api_key, plain_key = sample_api_key

        # Create event
        now = datetime.utcnow()
        event_resp = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Ownership Test",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "ownership-test",
            },
        )
        event_id = event_resp.json()["id"]

        # Create another user
        from app.database.models import APIKey, User

        other_user = User(username="other-upsert-user", type="api")
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        other_key = APIKey(
            user_id=other_user.id,
            key_hash=_hash_api_key("other-upsert-key"),
            name="other-key",
        )
        test_db.add(other_key)
        test_db.commit()

        # Try to upsert with other user's key
        response = client.post(
            f"/api/v2/events/{event_id}/sessions/sync",
            headers={"Authorization": "Bearer other-upsert-key"},
            json={
                "title": "Hacked Session",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "uri": "hacked-uri",
            },
        )
        assert response.status_code == HTTP_403_FORBIDDEN
