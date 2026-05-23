"""Integration tests for self-managed API key endpoints."""

from datetime import datetime, timedelta

import pytest


def _jwt_for_tests(sub: str = "kc-user-1") -> str:
    """Generate unsigned JWT-looking token for tests.

    We disable signature verification in these tests via settings patch.
    """
    from jose import jwt

    return jwt.encode(
        {
            "sub": sub,
            "preferred_username": "human-editor",
            "email": "human@example.com",
            "roles": ["summaraizer_admin", "editor"],
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        },
        key="test-secret",
        algorithm="HS256",
    )


@pytest.mark.integration
class TestAPIKeyManagement:
    """Test suite for /api/v2/me/api-keys endpoints."""

    def test_create_and_list_and_revoke_api_key(self, client, monkeypatch):
        """Interactive user can create/list/revoke own API keys."""
        from app.config.settings import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "jwt_verify_signature", False)
        monkeypatch.setattr(settings, "jwt_algorithms", ["HS256"])
        monkeypatch.setattr(settings, "jwt_audience", "")
        monkeypatch.setattr(settings, "jwt_issuer", "")

        jwt_token = _jwt_for_tests(sub="kc-user-create-list-revoke")

        create_response = client.post(
            "/api/v2/me/api-keys",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"name": "my-key", "allowed_roles": ["editor"]},
        )
        assert create_response.status_code == 201
        create_data = create_response.json()
        assert create_data["name"] == "my-key"
        assert create_data["allowed_roles"] == ["editor"]
        assert isinstance(create_data["key"], str)
        assert len(create_data["key"]) > 20

        list_response = client.get(
            "/api/v2/me/api-keys",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert list_response.status_code == 200
        keys = list_response.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["id"] == create_data["id"]
        assert keys[0]["deleted_at"] is None

        revoke_response = client.delete(
            f"/api/v2/me/api-keys/{create_data['id']}",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert revoke_response.status_code == 204

        list_response_after = client.get(
            "/api/v2/me/api-keys",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert list_response_after.status_code == 200
        key_after = list_response_after.json()["keys"][0]
        assert key_after["deleted_at"] is not None

    def test_create_api_key_rejects_non_subset_roles(self, client, monkeypatch):
        """Requested delegated roles must be subset of owner roles."""
        from app.config.settings import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "jwt_verify_signature", False)
        monkeypatch.setattr(settings, "jwt_algorithms", ["HS256"])
        monkeypatch.setattr(settings, "jwt_audience", "")
        monkeypatch.setattr(settings, "jwt_issuer", "")

        jwt_token = _jwt_for_tests(sub="kc-user-subset-check")

        response = client.post(
            "/api/v2/me/api-keys",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"name": "bad-key", "allowed_roles": ["superadmin"]},
        )

        assert response.status_code == 400
        assert "subset" in response.json()["detail"].lower()

    def test_api_key_management_requires_jwt_not_api_key(self, client, sample_api_key):
        """API key auth may not be used to manage API keys."""
        _, plain_key = sample_api_key

        response = client.get(
            "/api/v2/me/api-keys",
            headers={"Authorization": f"Bearer {plain_key}"},
        )

        assert response.status_code == 403
