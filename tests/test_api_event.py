"""Tests for Event API endpoints."""

import pytest
from datetime import datetime, timedelta
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)


class TestEventAPI:
    """Test suite for Event API endpoints."""

    def test_create_event_endpoint(self, client, sample_api_key):
        """Test creating an event via API."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "API Test Event",
                "description": "Test via API",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "location": "Test Location",
                "uri": "api-test-event",
            },
        )

        assert response.status_code == HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "API Test Event"
        assert data["uri"] == "api-test-event"
        assert data["id"] is not None

    def test_create_event_duplicate_uri(self, client, sample_api_key, sample_event):
        """Test creating an event with duplicate URI."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Another Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": sample_event.uri,  # Duplicate URI
            },
        )

        assert response.status_code == HTTP_409_CONFLICT

    def test_create_event_invalid_dates(self, client):
        """Test creating an event with invalid date range."""
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            json={
                "title": "Invalid Event",
                "start_date": now.isoformat(),
                "end_date": (now - timedelta(days=1)).isoformat(),  # End before start
                "uri": "invalid-event",
            },
        )

        assert response.status_code != HTTP_201_CREATED

    def test_get_event_by_id(self, client, sample_event):
        """Test getting an event by ID."""
        response = client.get(f"/api/v2/events/{sample_event.id}")

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["id"] == sample_event.id
        assert data["title"] == sample_event.title

    def test_get_event_by_id_not_found(self, client):
        """Test getting a non-existent event."""
        response = client.get("/api/v2/events/999")

        assert response.status_code == HTTP_404_NOT_FOUND

    def test_get_event_by_uri(self, client, sample_event):
        """Test getting an event by URI."""
        response = client.get(f"/api/v2/events/by-uri/{sample_event.uri}")

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["uri"] == sample_event.uri

    def test_list_events(self, client, sample_event):
        """Test listing events."""
        response = client.get("/api/v2/events")

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        assert sample_event.id in [e["id"] for e in data]

    def test_list_events_with_pagination(self, client):
        """Test listing events with pagination."""
        # Create multiple events
        now = datetime.utcnow()
        for i in range(5):
            client.post(
                "/api/v2/events",
                json={
                    "title": f"Event {i}",
                    "start_date": now.isoformat(),
                    "end_date": (now + timedelta(days=1)).isoformat(),
                    "uri": f"event-{i}",
                },
            )

        # Test pagination
        response = client.get("/api/v2/events?skip=0&limit=2")
        assert response.status_code == HTTP_200_OK
        assert len(response.json()) <= 2

    def test_update_event(self, client, sample_api_key, event_with_owner):
        """Test updating an event."""
        api_key, plain_key = sample_api_key
        response = client.patch(
            f"/api/v2/events/{event_with_owner.id}",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Updated Event Title",
                "status": "published",
            },
        )

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["title"] == "Updated Event Title"
        assert data["status"] == "published"

    def test_update_event_not_found(self, client, sample_api_key):
        """Test updating a non-existent event."""
        api_key, plain_key = sample_api_key
        response = client.patch(
            "/api/v2/events/999",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={"title": "Updated"},
        )

        assert response.status_code == HTTP_404_NOT_FOUND

    def test_delete_event(self, client, sample_api_key, event_with_owner):
        """Test deleting an event."""
        api_key, plain_key = sample_api_key
        event_id = event_with_owner.id
        response = client.delete(
            f"/api/v2/events/{event_id}",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_204_NO_CONTENT

        # Verify deletion
        get_response = client.get(f"/api/v2/events/{event_id}")
        assert get_response.status_code == HTTP_404_NOT_FOUND

    def test_delete_event_not_found(self, client, sample_api_key):
        """Test deleting a non-existent event."""
        api_key, plain_key = sample_api_key
        response = client.delete(
            "/api/v2/events/999",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == HTTP_404_NOT_FOUND
