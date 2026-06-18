"""Integration tests for owner-scoped dashboard list endpoints."""

from datetime import datetime, timedelta

import pytest

from app.database.models import Event, SessionOwner
from app.database.models import Session as SessionModel


@pytest.mark.integration
class TestDashboardScopedLists:
    """Test suite for /api/v2/events/me and /api/v2/sessions/me."""

    def test_events_me_returns_owner_scoped_paginated_list(self, client, sample_api_key):
        """Non-admin users should see only their own events with pagination meta."""
        _, plain_key = sample_api_key
        now = datetime.utcnow()

        for idx in range(3):
            response = client.post(
                "/api/v2/events",
                headers={"Authorization": f"Bearer {plain_key}"},
                json={
                    "title": f"My Event {idx}",
                    "start_date": now.isoformat(),
                    "end_date": (now + timedelta(days=1)).isoformat(),
                    "uri": f"my-event-{idx}",
                },
            )
            assert response.status_code == 201

        response = client.get(
            "/api/v2/events/me?skip=0&limit=2&sort_by=created_at&sort_dir=asc",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["items"]) == 2
        assert payload["meta"]["total"] >= 3
        assert payload["meta"]["skip"] == 0
        assert payload["meta"]["limit"] == 2
        assert payload["meta"]["has_more"] is True

    def test_events_me_admin_can_see_all(self, client, test_db, sample_user, sample_api_key):
        """Admin role should allow viewing all events regardless of owner."""
        _, plain_key = sample_api_key

        sample_user.roles = ["summaraizer_admin"]
        test_db.commit()

        other_user = type(sample_user)(
            username="dashboard-other-user", type="human", is_active=True
        )
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        now = datetime.utcnow()
        other_event = Event(
            title="Other Owner Event",
            start_date=now,
            end_date=now + timedelta(days=1),
            uri="other-owner-event",
            owner_id=other_user.id,
        )
        test_db.add(other_event)
        test_db.commit()

        response = client.get(
            "/api/v2/events/me",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["meta"]["total"] >= 1
        assert any(item["id"] == other_event.id for item in payload["items"])

    def test_sessions_me_includes_event_owned_sessions(
        self, client, test_db, sample_api_key, sample_user
    ):
        """Event owner should see sessions in owned event even when session owner differs."""
        _, plain_key = sample_api_key

        now = datetime.utcnow()
        event_response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Owner Scope Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "owner-scope-event",
            },
        )
        assert event_response.status_code == 201
        event_id = event_response.json()["id"]

        foreign_owner = type(sample_user)(
            username="dashboard-session-owner", type="human", is_active=True
        )
        test_db.add(foreign_owner)
        test_db.commit()
        test_db.refresh(foreign_owner)

        foreign_session = SessionModel(
            title="Foreign Owned In My Event",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            status="draft",
            uri="foreign-owned-in-my-event",
            event_id=event_id,
        )
        test_db.add(foreign_session)
        test_db.commit()
        test_db.refresh(foreign_session)
        test_db.add(
            SessionOwner(
                session_id=foreign_session.id,
                user_id=foreign_owner.id,
                added_by_user_id=foreign_owner.id,
            )
        )
        test_db.commit()

        response = client.get(
            "/api/v2/sessions/me?sort_by=created_at&sort_dir=asc",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["meta"]["total"] >= 1
        assert any(item["id"] == foreign_session.id for item in payload["items"])

    def test_sessions_me_pagination_metadata(self, client, sample_api_key, sample_event):
        """Sessions /me should include stable pagination metadata."""
        _, plain_key = sample_api_key
        now = datetime.utcnow()

        for idx in range(3):
            response = client.post(
                "/api/v2/sessions",
                headers={"Authorization": f"Bearer {plain_key}"},
                json={
                    "title": f"Scoped Session {idx}",
                    "start_datetime": now.isoformat(),
                    "end_datetime": (now + timedelta(hours=1)).isoformat(),
                    "uri": f"scoped-session-{idx}",
                    "event_id": sample_event.id,
                    "status": "draft",
                },
            )
            assert response.status_code == 201

        response = client.get(
            "/api/v2/sessions/me?skip=0&limit=2&sort_by=created_at&sort_dir=asc",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["items"]) == 2
        assert payload["meta"]["total"] >= 3
        assert payload["meta"]["has_more"] is True
