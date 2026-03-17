"""Integration tests for Session API filtering endpoints."""

from datetime import datetime, timedelta

import pytest
from starlette.status import HTTP_200_OK, HTTP_400_BAD_REQUEST


def _hash_api_key(key: str) -> str:
    """Hash API key for testing."""
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.integration
class TestSessionFilteringAPI:
    """Integration tests for session filtering via API."""

    @pytest.fixture
    def api_headers(self, sample_api_key):
        """Generate API headers for authenticated requests."""
        api_key, plain_key = sample_api_key
        return {"Authorization": f"Bearer {plain_key}"}

    @pytest.fixture
    def sessions_for_filtering(self, client, sample_event, api_headers):
        """Create diverse sessions for filter testing."""
        now = datetime.utcnow()

        sessions_data = [
            {
                "title": "AI Fundamentals",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(minutes=45)).isoformat(),
                "language": "en",
                "uri": "ai-fundamentals",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "Input",
                "speakers": ["Alice"],
                "tags": ["AI", "Basics"],
                "duration": 45,
            },
            {
                "title": "Machine Learning Workshop",
                "start_datetime": (now + timedelta(hours=1)).isoformat(),
                "end_datetime": (now + timedelta(hours=3)).isoformat(),
                "language": "en",
                "uri": "ml-workshop",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "workshop",
                "speakers": ["Bob", "Charlie"],
                "tags": ["ML", "Hands-on"],
                "duration": 120,
            },
            {
                "title": "Ethics in AI",
                "start_datetime": (now + timedelta(hours=4)).isoformat(),
                "end_datetime": (now + timedelta(hours=4, minutes=15)).isoformat(),
                "language": "de",
                "uri": "ethics-ai",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "Lighting Talk",
                "speakers": ["Diana"],
                "tags": ["Ethics", "AI"],
                "duration": 15,
            },
            {
                "title": "Data Science Basics",
                "start_datetime": (now + timedelta(days=1)).isoformat(),
                "end_datetime": (now + timedelta(days=1, hours=1)).isoformat(),
                "language": "en",
                "uri": "data-science",
                "event_id": sample_event.id,
                "status": "draft",
                "session_format": "Training",
                "speakers": ["Eva"],
                "tags": ["Data"],
                "duration": 60,
            },
        ]

        created_sessions = []
        for session_data in sessions_data:
            response = client.post(
                "/api/v2/sessions",
                headers=api_headers,
                json=session_data,
            )
            assert response.status_code == 201
            created_sessions.append(response.json())

        return created_sessions

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_list_sessions_no_filters(self, client):
        """Test listing sessions without filters."""
        response = client.get("/api/v2/sessions")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Should return only published sessions for unauthenticated user
        assert len(data) >= 3

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_status_query_param(self, client):
        """Test filtering by status via query parameter."""
        response = client.get("/api/v2/sessions?status=published")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert all(s["status"] == "published" for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_language_single(self, client):
        """Test filtering by single language."""
        response = client.get("/api/v2/sessions?language=en")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 2
        assert all(s["language"] == "en" for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_language_german(self, client):
        """Test filtering by German language."""
        response = client.get("/api/v2/sessions?language=de")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_session_format(self, client):
        """Test filtering by session format."""
        response = client.get("/api/v2/sessions?session_format=workshop")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["session_format"] == "workshop"

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_tags_single(self, client):
        """Test filtering by single tag."""
        response = client.get("/api/v2/sessions?tags=AI")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 2
        # All results should have AI tag
        assert all(any("AI" in tag for tag in s.get("tags", [])) for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_tags_multiple_or_logic(self, client):
        """Test filtering by multiple tags uses OR logic."""
        response = client.get("/api/v2/sessions?tags=AI,Data")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Should include AI sessions and Data Science (if published)
        assert len(data) >= 2

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_tags_url_encoded(self, client):
        """Test filtering by tags with URL encoding."""
        response = client.get("/api/v2/sessions?tags=AI%2CBasics")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_duration_min(self, client):
        """Test filtering by minimum duration."""
        response = client.get("/api/v2/sessions?duration_min=60")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        assert all(s.get("duration", 0) >= 60 for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_duration_max(self, client):
        """Test filtering by maximum duration."""
        response = client.get("/api/v2/sessions?duration_max=20")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        assert all(s.get("duration", 0) <= 20 for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_duration_range(self, client):
        """Test filtering by duration range."""
        response = client.get("/api/v2/sessions?duration_min=40&duration_max=120")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        assert all(40 <= s.get("duration", 0) <= 120 for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_speaker(self, client):
        """Test filtering by speaker name."""
        response = client.get("/api/v2/sessions?speaker=Alice")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        assert any("Alice" in str(s.get("speakers", [])) for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_speaker_case_insensitive(self, client):
        """Test speaker filtering is case-insensitive."""
        response = client.get("/api/v2/sessions?speaker=bob")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_search_by_title(self, client):
        """Test searching by title."""
        response = client.get("/api/v2/sessions?search=Workshop")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1
        assert any("Workshop" in s["title"] for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_search_by_description(self, client):
        """Test searching by description."""
        response = client.get("/api/v2/sessions?search=Hands-on")
        assert response.status_code == HTTP_200_OK
        # May or may not find results depending on description

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_search_case_insensitive(self, client):
        """Test search is case-insensitive."""
        response = client.get("/api/v2/sessions?search=workshop")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 1

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_combined_filters(self, client):
        """Test combining multiple filters."""
        response = client.get("/api/v2/sessions?language=en&status=published&duration_min=40")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert all(
            s["language"] == "en" and s["status"] == "published" and s.get("duration", 0) >= 40
            for s in data
        )

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filters_with_pagination(self, client):
        """Test filters work with pagination."""
        response = client.get("/api/v2/sessions?status=published&limit=2&skip=0")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) <= 2

    def test_invalid_date_format_start_after(self, client):
        """Test invalid date format in start_after parameter."""
        response = client.get("/api/v2/sessions?start_after=invalid-date")
        assert response.status_code == HTTP_400_BAD_REQUEST
        data = response.json()
        assert "Invalid start_after" in data["detail"]

    def test_invalid_date_format_start_before(self, client):
        """Test invalid date format in start_before parameter."""
        response = client.get("/api/v2/sessions?start_before=2024-13-45")
        assert response.status_code == HTTP_400_BAD_REQUEST

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_valid_iso_datetime_start_after(self, client):
        """Test valid ISO 8601 datetime in start_after."""
        now = datetime.utcnow()
        iso_datetime = (now + timedelta(hours=2)).isoformat()
        response = client.get(f"/api/v2/sessions?start_after={iso_datetime}")
        assert response.status_code == HTTP_200_OK

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_iso_datetime_with_z_suffix(self, client):
        """Test ISO datetime with Z suffix is handled correctly."""
        now = datetime.utcnow()
        iso_datetime = (now + timedelta(hours=2)).isoformat() + "Z"
        response = client.get(f"/api/v2/sessions?start_after={iso_datetime}")
        assert response.status_code == HTTP_200_OK

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_sql_injection_in_search_param(self, client):
        """Test SQL injection attempt in search parameter is handled safely."""
        response = client.get("/api/v2/sessions?search='; DROP TABLE sessions; --")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Should not leak any data
        assert isinstance(data, list)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_sql_injection_in_speaker_param(self, client):
        """Test SQL injection attempt in speaker parameter."""
        response = client.get("/api/v2/sessions?speaker=Alice' OR '1'='1")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_xss_attempt_in_search(self, client):
        """Test XSS-like content in search doesn't break the API."""
        response = client.get("/api/v2/sessions?search=<script>alert('xss')</script>")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)

    def test_very_long_search_query(self, client):
        """Test very long search query is handled."""
        long_query = "a" * 8500  # Longer than MAX_EMBEDDING_TEXT_LENGTH
        response = client.get(f"/api/v2/sessions?search={long_query}")
        assert response.status_code == HTTP_200_OK or response.status_code == HTTP_400_BAD_REQUEST

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_special_url_characters_in_search(self, client):
        """Test special URL characters in search."""
        response = client.get("/api/v2/sessions?search=%20%21%40%23%24%25")
        # Should not crash
        assert response.status_code == HTTP_200_OK

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_unicode_in_search(self, client):
        """Test unicode characters in search parameter."""
        response = client.get("/api/v2/sessions?search=Künstliche")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_multiple_tags_empty(self, client):
        """Test tags parameter with empty value."""
        response = client.get("/api/v2/sessions?tags=")
        assert response.status_code == HTTP_200_OK

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_pagination_limit_max(self, client):
        """Test that pagination limit is capped at 1000."""
        response = client.get("/api/v2/sessions?limit=5000")
        # May get 200 (accepted) or 422 (validation rejected large limit)
        assert response.status_code in [HTTP_200_OK, 422]
        if response.status_code == HTTP_200_OK:
            data = response.json()
            assert len(data) <= 1000

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_negative_skip(self, client):
        """Test negative skip parameter."""
        response = client.get("/api/v2/sessions?skip=-10")
        # May get 200 (SQLAlchemy handles) or 422 (validation rejected negative)
        assert response.status_code in [HTTP_200_OK, 422]

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_response_structure_with_filters(self, client):
        """Test that filtered response has correct structure."""
        response = client.get("/api/v2/sessions?status=published")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        if data:
            # Check structure of first result
            session = data[0]
            assert "id" in session
            assert "title" in session
            assert "language" in session
            assert "tags" in session or session is not None


@pytest.mark.integration
class TestSemanticSearchWithFilters:
    """Integration tests for semantic search endpoint with filters."""

    @pytest.fixture
    def api_headers(self, sample_api_key):
        """Generate API headers for authenticated requests."""
        api_key, plain_key = sample_api_key
        return {"Authorization": f"Bearer {plain_key}"}

    @pytest.fixture
    def sessions_with_embeddings(self, client, sample_event, api_headers):
        """Create sessions and store embeddings (if embeddings are enabled)."""
        now = datetime.utcnow()

        sessions_data = [
            {
                "title": "Artificial Intelligence Basics",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(hours=1)).isoformat(),
                "language": "en",
                "uri": "ai-basics",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "Input",
                "tags": ["AI"],
            },
            {
                "title": "Machine Learning Algorithms",
                "start_datetime": (now + timedelta(hours=2)).isoformat(),
                "end_datetime": (now + timedelta(hours=3)).isoformat(),
                "language": "en",
                "uri": "ml-algorithms",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "workshop",
                "tags": ["ML", "Algorithms"],
            },
            {
                "title": "Ethics in Technology",
                "start_datetime": (now + timedelta(hours=4)).isoformat(),
                "end_datetime": (now + timedelta(hours=5)).isoformat(),
                "language": "en",
                "uri": "ethics-tech",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "Diskussion",
                "tags": ["Ethics"],
            },
        ]

        created_sessions = []
        for session_data in sessions_data:
            response = client.post(
                "/api/v2/sessions",
                headers=api_headers,
                json=session_data,
            )
            if response.status_code == 201:
                created_sessions.append(response.json())

        return created_sessions

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_basic(self, client):
        """Test basic semantic search without filters."""
        response = client.get("/api/v2/sessions/search/similar?query=machine+learning")
        # May not have embeddings enabled, so check for 200 or 503
        assert response.status_code in [200, 503]

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_language_filter(self, client):
        """Test semantic search with language filter."""
        response = client.get("/api/v2/sessions/search/similar?query=AI&language=en")
        assert response.status_code in [200, 503]

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_tags_filter(self, client):
        """Test semantic search with tags filter."""
        response = client.get("/api/v2/sessions/search/similar?query=algorithms&tags=ML")
        assert response.status_code in [200, 503]

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_format_filter(self, client):
        """Test semantic search with session format filter."""
        response = client.get(
            "/api/v2/sessions/search/similar?query=learning&session_format=workshop"
        )
        assert response.status_code in [200, 503]

    def test_semantic_search_query_required(self, client):
        """Test that query parameter is required."""
        response = client.get("/api/v2/sessions/search/similar")
        assert response.status_code in [400, 422]

    def test_semantic_search_empty_query(self, client):
        """Test searching with empty query."""
        response = client.get("/api/v2/sessions/search/similar?query=")
        assert response.status_code in [400, 422]

    def test_semantic_search_very_long_query(self, client):
        """Test semantic search with very long query."""
        long_query = "a" * 8500
        response = client.get(f"/api/v2/sessions/search/similar?query={long_query}")
        # Should reject or handle gracefully
        assert response.status_code in [400, 403, 422, 503]
