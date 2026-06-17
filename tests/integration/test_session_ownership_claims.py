"""Integration tests for session ownership claims and multi-owner management."""

import hashlib
from datetime import datetime, timedelta

import pytest

from app.database.models import APIKey, User


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _create_user_with_api_key(test_db, username: str, key: str) -> tuple[User, str]:
    user = User(username=username, type="human", is_active=True)
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    api_key = APIKey(user_id=user.id, key_hash=_hash_api_key(key), name=f"{username}-key")
    test_db.add(api_key)
    test_db.commit()
    return user, key


def _create_event(client, key: str, uri: str) -> int:
    now = datetime.utcnow()
    response = client.post(
        "/api/v2/events",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "title": f"Event {uri}",
            "start_date": now.isoformat(),
            "end_date": (now + timedelta(days=1)).isoformat(),
            "uri": uri,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_session(client, key: str, event_id: int, uri: str) -> int:
    now = datetime.utcnow()
    response = client.post(
        f"/api/v2/events/{event_id}/sessions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "title": f"Session {uri}",
            "start_datetime": now.isoformat(),
            "end_datetime": (now + timedelta(hours=1)).isoformat(),
            "uri": uri,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.integration
class TestSessionOwnershipClaims:
    """End-to-end tests for claims and owner management endpoints."""

    def test_claim_approve_grants_session_manage_access(self, client, test_db):
        """Approved claims should grant owner permissions; pending should not."""
        owner, owner_key = _create_user_with_api_key(test_db, "owner-claims-e2e", "owner-key-1")
        claimant, claimant_key = _create_user_with_api_key(
            test_db, "claimant-claims-e2e", "claimant-key-1"
        )
        event_id = _create_event(client, owner_key, "claims-event-approve")
        session_id = _create_session(client, owner_key, event_id, "claims-session-approve")

        claim_response = client.post(
            f"/api/v2/sessions/{session_id}/ownership-claims",
            headers={"Authorization": f"Bearer {claimant_key}"},
            json={"request_note": "I am one of the speakers"},
        )
        assert claim_response.status_code == 200
        claim_id = claim_response.json()["id"]

        denied_update = client.patch(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {claimant_key}"},
            json={"title": "Should fail while pending"},
        )
        assert denied_update.status_code == 403

        approve_response = client.post(
            f"/api/v2/sessions/{session_id}/ownership-claims/{claim_id}/approve",
            headers={"Authorization": f"Bearer {owner_key}"},
            json={"review_note": "approved"},
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

        allowed_update = client.patch(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {claimant_key}"},
            json={"title": "Updated by approved claimant"},
        )
        assert allowed_update.status_code == 200
        assert allowed_update.json()["title"] == "Updated by approved claimant"

        owners_response = client.get(
            f"/api/v2/sessions/{session_id}/owners",
            headers={"Authorization": f"Bearer {owner_key}"},
        )
        assert owners_response.status_code == 200
        owner_ids = [item["user_id"] for item in owners_response.json()]
        assert owner.id in owner_ids
        assert claimant.id in owner_ids

    def test_claim_reject_keeps_manage_access_denied(self, client, test_db):
        """Rejected claims should not grant owner permissions."""
        _, owner_key = _create_user_with_api_key(test_db, "owner-claims-reject", "owner-key-2")
        _, claimant_key = _create_user_with_api_key(
            test_db, "claimant-claims-reject", "claimant-key-2"
        )
        event_id = _create_event(client, owner_key, "claims-event-reject")
        session_id = _create_session(client, owner_key, event_id, "claims-session-reject")

        claim_response = client.post(
            f"/api/v2/sessions/{session_id}/ownership-claims",
            headers={"Authorization": f"Bearer {claimant_key}"},
            json={"request_note": "please"},
        )
        assert claim_response.status_code == 200
        claim_id = claim_response.json()["id"]

        reject_response = client.post(
            f"/api/v2/sessions/{session_id}/ownership-claims/{claim_id}/reject",
            headers={"Authorization": f"Bearer {owner_key}"},
            json={"review_note": "not enough evidence"},
        )
        assert reject_response.status_code == 200
        assert reject_response.json()["status"] == "rejected"

        denied_update = client.patch(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {claimant_key}"},
            json={"title": "Should still fail"},
        )
        assert denied_update.status_code == 403

    def test_existing_owner_can_add_and_remove_other_owners(self, client, test_db):
        """Current session owners can manage additional owners."""
        _, owner_key = _create_user_with_api_key(test_db, "owner-manage-owner", "owner-key-3")
        target_user, target_key = _create_user_with_api_key(
            test_db, "target-manage-owner", "target-key-3"
        )

        event_id = _create_event(client, owner_key, "claims-event-owner-mgmt")
        session_id = _create_session(client, owner_key, event_id, "claims-session-owner-mgmt")

        add_response = client.post(
            f"/api/v2/sessions/{session_id}/owners",
            headers={"Authorization": f"Bearer {owner_key}"},
            json={"user_id": target_user.id},
        )
        assert add_response.status_code == 200
        assert add_response.json()["user_id"] == target_user.id

        update_allowed = client.patch(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {target_key}"},
            json={"title": "Updated by manually added owner"},
        )
        assert update_allowed.status_code == 200

        remove_response = client.delete(
            f"/api/v2/sessions/{session_id}/owners/{target_user.id}",
            headers={"Authorization": f"Bearer {owner_key}"},
        )
        assert remove_response.status_code == 204

        update_denied = client.patch(
            f"/api/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {target_key}"},
            json={"title": "Should fail after remove"},
        )
        assert update_denied.status_code == 403
