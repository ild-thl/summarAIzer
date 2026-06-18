"""Tests for authentication, authorization, and user/API key management."""

import hashlib
from datetime import datetime, timedelta

from starlette.status import (
    HTTP_201_CREATED,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
)


def _hash_api_key(key: str) -> str:
    """Hash API key for testing."""
    return hashlib.sha256(key.encode()).hexdigest()


class TestUserAndAPIKeyModels:
    """Test suite for User and APIKey models."""

    def test_create_user(self, test_db):
        """Test creating a user."""
        from app.database.models import User

        user = User(
            username="testuser",
            email="test@example.com",
            type="api",
            is_active=True,
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        assert user.id is not None
        assert user.username == "testuser"
        assert user.type == "api"
        assert user.is_active is True

    def test_create_api_key(self, test_db, sample_user):
        """Test creating an API key."""
        from app.database.models import APIKey

        api_key = APIKey(
            user_id=sample_user.id,
            key_hash=_hash_api_key("test-key"),
            name="test-key",
        )
        test_db.add(api_key)
        test_db.commit()
        test_db.refresh(api_key)

        assert api_key.id is not None
        assert api_key.user_id == sample_user.id
        assert api_key.name == "test-key"

    def test_user_can_have_multiple_api_keys(self, test_db, sample_user):
        """Test that a user can have multiple API keys."""
        from app.database.models import APIKey

        key1 = APIKey(
            user_id=sample_user.id,
            key_hash=_hash_api_key("key-1"),
            name="key-1",
        )
        key2 = APIKey(
            user_id=sample_user.id,
            key_hash=_hash_api_key("key-2"),
            name="key-2",
        )
        test_db.add_all([key1, key2])
        test_db.commit()

        # Refresh user to load api_keys relationship
        test_db.refresh(sample_user)
        assert len(sample_user.api_keys) == 2

    def test_api_key_soft_delete(self, test_db, sample_user):
        """Test soft delete of API key."""
        from app.database.models import APIKey

        api_key = APIKey(
            user_id=sample_user.id,
            key_hash=_hash_api_key("test-key"),
            name="test-key",
        )
        test_db.add(api_key)
        test_db.commit()
        test_db.refresh(api_key)

        # Soft delete
        api_key.deleted_at = datetime.utcnow()
        test_db.commit()
        test_db.refresh(api_key)

        assert api_key.deleted_at is not None


class TestAuthenticationMiddleware:
    """Test suite for authentication middleware."""

    def test_missing_authorization_header(self, client):
        """Test request without authorization header returns 401 for protected endpoints."""
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            json={
                "title": "Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "test-event",
            },
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_invalid_authorization_format(self, client):
        """Test request with invalid authorization format returns 401."""
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": "InvalidFormat"},
            json={
                "title": "Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "test-event",
            },
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_invalid_api_key(self, client):
        """Test request with invalid API key returns 401."""
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": "Bearer invalid-key-12345"},
            json={
                "title": "Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "test-event",
            },
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_valid_authentication(self, client, sample_api_key):
        """Test successful authentication."""
        api_key, plain_key = sample_api_key

        now = datetime.utcnow()
        # Try to create an event with valid auth
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Authenticated Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "auth-test",
            },
        )
        # Should get 201 (not 401)
        assert response.status_code != HTTP_401_UNAUTHORIZED

    def test_inactive_user(self, test_db, client, sample_api_key):
        """Test authentication with inactive user returns 401."""
        api_key, plain_key = sample_api_key

        # Deactivate user
        user = api_key.user
        user.is_active = False
        test_db.commit()

        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "test-event",
            },
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_soft_deleted_api_key(self, test_db, client, sample_api_key):
        """Test authentication with soft-deleted API key returns 401."""
        api_key, plain_key = sample_api_key

        # Soft delete the key
        api_key.deleted_at = datetime.utcnow()
        test_db.commit()

        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "test-event",
            },
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED


class TestEventOwnershipAndCreation:
    """Test suite for event ownership and creation."""

    def test_create_event_requires_auth(self, client):
        """Test that creating an event requires authentication."""
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            json={
                "title": "Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "test-event",
            },
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_create_event_with_auth(self, client, sample_user, sample_api_key):
        """Test creating an event with valid authentication."""
        api_key, plain_key = sample_api_key
        now = datetime.utcnow()

        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Authenticated Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "auth-event",
            },
        )
        assert response.status_code == HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "Authenticated Event"
        assert data["owner_id"] == sample_user.id

    def test_update_event_requires_ownership(
        self,
        client,
        test_db,
        sample_api_key,
    ):
        """Test that updating an event requires ownership."""
        api_key, plain_key = sample_api_key

        # Create event with this user
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "My Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "my-event",
            },
        )
        event_id = response.json()["id"]

        # Create another user's API key
        from app.database.models import APIKey, User

        other_user = User(username="other-user", type="api")
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        other_key = APIKey(
            user_id=other_user.id,
            key_hash=_hash_api_key("other-key"),
            name="other-key",
        )
        test_db.add(other_key)
        test_db.commit()

        # Try to update with other user's key
        response = client.patch(
            f"/api/v2/events/{event_id}",
            headers={"Authorization": "Bearer other-key"},
            json={"title": "Hacked Event"},
        )
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_delete_event_requires_ownership(
        self,
        client,
        test_db,
        sample_api_key,
    ):
        """Test that deleting an event requires ownership."""
        api_key, plain_key = sample_api_key

        # Create event
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {plain_key}"},
            json={
                "title": "Delete Test Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "delete-test-event",
            },
        )
        event_id = response.json()["id"]

        # Create another user
        from app.database.models import APIKey, User

        other_user = User(username="other-user-2", type="api")
        test_db.add(other_user)
        test_db.commit()
        test_db.refresh(other_user)

        other_key = APIKey(
            user_id=other_user.id,
            key_hash=_hash_api_key("other-key-2"),
            name="other-key",
        )
        test_db.add(other_key)
        test_db.commit()

        # Try to delete with other user's key
        response = client.delete(
            f"/api/v2/events/{event_id}",
            headers={"Authorization": "Bearer other-key-2"},
        )
        assert response.status_code == HTTP_403_FORBIDDEN


class TestAuthorizationHierarchy:
    """Test suite for admin and ownership hierarchy checks."""

    def test_event_owner_can_update_session_in_owned_event(
        self,
        client,
        test_db,
        sample_api_key,
    ):
        """Event owner may update any session belonging to that event."""
        _, owner_key = sample_api_key

        # Create event owned by sample user (authenticated via sample_api_key)
        now = datetime.utcnow()
        event_response = client.post(
            "/api/v2/events",
            headers={"Authorization": f"Bearer {owner_key}"},
            json={
                "title": "Hierarchy Event",
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=1)).isoformat(),
                "uri": "hierarchy-event",
            },
        )
        assert event_response.status_code == HTTP_201_CREATED
        event_id = event_response.json()["id"]

        from app.database.models import Session as SessionModel
        from app.database.models import SessionOwner, User

        # Create a different user as direct session owner
        session_owner = User(username="hierarchy-session-owner", type="api")
        test_db.add(session_owner)
        test_db.commit()
        test_db.refresh(session_owner)

        # Session belongs to event owned by sample user, but has different owner_id
        foreign_owned_session = SessionModel(
            title="Foreign Owned Session",
            speakers=[],
            tags=[],
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            uri="foreign-owned-session",
            event_id=event_id,
        )
        test_db.add(foreign_owned_session)
        test_db.commit()
        test_db.refresh(foreign_owned_session)
        test_db.add(
            SessionOwner(
                session_id=foreign_owned_session.id,
                user_id=session_owner.id,
                added_by_user_id=session_owner.id,
            )
        )
        test_db.commit()

        response = client.patch(
            f"/api/v2/sessions/{foreign_owned_session.id}",
            headers={"Authorization": f"Bearer {owner_key}"},
            json={"title": "Updated by Event Owner"},
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Updated by Event Owner"

    def test_is_admin_uses_jwt_auth_context(self, sample_user):
        """Admin role is resolved from JWT auth context."""
        from app.security.auth import _set_auth_context, is_admin

        _set_auth_context(method="jwt", roles=["summaraizer_admin"])
        assert is_admin(sample_user) is True

        _set_auth_context(method="jwt", roles=["editor"])
        assert is_admin(sample_user) is False

    def test_api_key_inherits_owner_admin_role(self, test_db, sample_user):
        """API key auth inherits owner roles by default."""
        from app.database.models import APIKey
        from app.security.auth import _authenticate_with_api_key, is_admin

        sample_user.roles = ["summaraizer_admin"]
        sample_user.groups = ["/admin"]
        test_db.commit()

        plain_key = "delegated-admin-key"
        api_key = APIKey(
            user_id=sample_user.id,
            key_hash=_hash_api_key(plain_key),
            name="delegated-admin-key",
        )
        test_db.add(api_key)
        test_db.commit()

        authenticated_user = _authenticate_with_api_key(plain_key, test_db)
        assert authenticated_user.id == sample_user.id
        assert is_admin(authenticated_user) is True

    def test_api_key_role_subset_cannot_escalate_privileges(self, test_db, sample_user):
        """API key optional role subset can only reduce owner privileges."""
        from app.database.models import APIKey
        from app.security.auth import _authenticate_with_api_key, is_admin

        sample_user.roles = ["summaraizer_admin", "editor"]
        test_db.commit()

        plain_key = "delegated-editor-key"
        api_key = APIKey(
            user_id=sample_user.id,
            key_hash=_hash_api_key(plain_key),
            name="delegated-editor-key",
            allowed_roles=["editor"],
        )
        test_db.add(api_key)
        test_db.commit()

        authenticated_user = _authenticate_with_api_key(plain_key, test_db)
        assert authenticated_user.id == sample_user.id
        assert is_admin(authenticated_user) is False
