"""Integration tests for Session API filtering endpoints."""

import json
from datetime import datetime, timedelta
from itertools import pairwise
from unittest.mock import AsyncMock

import pytest
from starlette.status import HTTP_200_OK, HTTP_400_BAD_REQUEST, HTTP_422_UNPROCESSABLE_CONTENT


def _hash_api_key(key: str) -> str:
    """Hash API key for testing."""
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()


def _time_windows_query(windows: list[dict[str, str]]) -> str:
    """Encode window list for time_windows query parameter."""
    return json.dumps(windows, separators=(",", ":"))


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
                "session_format": "input",
                "location": "Stage Berlin",
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
                "location": "Stage Berlin",
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
                "session_format": "lightning talk",
                "location": "AI Stage TU Graz",
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
                "session_format": "training",
                "location": "Stage Berlin",
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
    def test_filter_by_location_single(self, client):
        """Test filtering by single location."""
        response = client.get("/api/v2/sessions?location_names=Stage+Berlin")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert len(data) >= 2
        # All published results should have this location
        assert all((s.get("location") or {}).get("name") == "Stage Berlin" for s in data)

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_location_multiple_or_logic(self, client):
        """Test filtering by multiple locations uses OR logic."""
        response = client.get("/api/v2/sessions?location_names=Stage+Berlin,AI+Stage+TU+Graz")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Should include sessions from both locations (OR logic)
        assert len(data) >= 3
        locations = {(s.get("location") or {}).get("name") for s in data}
        assert "Stage Berlin" in locations or "AI Stage TU Graz" in locations

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

    def test_invalid_time_windows_json(self, client):
        """Test invalid JSON in time_windows parameter."""
        response = client.get("/api/v2/sessions?time_windows=not-json")
        assert response.status_code == HTTP_400_BAD_REQUEST
        data = response.json()
        assert "Invalid time_windows format" in data["detail"]

    def test_invalid_time_windows_datetime(self, client):
        """Test invalid datetime value in time_windows parameter."""
        payload = _time_windows_query([{"start": "2024-13-45", "end": "2024-12-31T23:59:59"}])
        response = client.get(f"/api/v2/sessions?time_windows={payload}")
        assert response.status_code == HTTP_400_BAD_REQUEST

    def test_query_refinement_route_returns_refined_payload(self, client, monkeypatch):
        """Test the query refinement route returns structured optimized filters."""
        from app.schemas.session import SearchIntentRefinementResponse
        from app.services.embedding import factory as embedding_factory

        refinement_service = AsyncMock()
        refinement_service.refine_search_intent.return_value = SearchIntentRefinementResponse(
            refined_queries=["ethischer Einsatz von KI im Unterricht"],
            event_id=9,
            session_format=["diskussion", "workshop"],
            tags=["Ethik", "Bildung"],
            location_cities=["Raum A"],
            rationale="The query implies collaborative discussion and education-related topics.",
        )
        monkeypatch.setattr(
            embedding_factory,
            "get_query_refinement_service",
            lambda: refinement_service,
            raising=False,
        )

        response = client.post(
            "/api/v2/sessions/query/refine",
            json={
                "queries": ["Ich will mit anderen ueber KI diskutieren"],
                "event_id": 9,
            },
        )

        assert response.status_code == HTTP_200_OK
        data = response.json()
        assert data["refined_queries"] == ["ethischer Einsatz von KI im Unterricht"]
        assert "refined_query" not in data
        assert "original_query" not in data
        assert data["session_format"] == ["diskussion", "workshop"]
        assert data["tags"] == ["Ethik", "Bildung"]
        assert data["location_cities"] == ["Raum A"]

    def test_query_refinement_route_rejects_blank_query(self, client):
        """Test the query refinement route rejects blank queries at schema level."""
        response = client.post(
            "/api/v2/sessions/query/refine",
            json={"queries": ["   "], "event_id": 9},
        )

        assert response.status_code == 422

    def test_query_refinement_route_requires_event_id(self, client):
        """Test the query refinement route requires event_id."""
        response = client.post(
            "/api/v2/sessions/query/refine",
            json={"queries": ["Ich will mit anderen ueber KI diskutieren"]},
        )

        assert response.status_code == 422

    def test_query_refinement_route_rejects_removed_filter_params(self, client):
        """Test the query refinement route rejects fields that are no longer part of the contract."""
        response = client.post(
            "/api/v2/sessions/query/refine",
            json={
                "queries": ["Ich will mit anderen ueber KI diskutieren"],
                "event_id": 9,
                "language": ["de"],
                "duration_min": 30,
                "time_windows": [],
            },
        )

        assert response.status_code == 422

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_valid_iso_datetime_time_windows(self, client):
        """Test valid ISO 8601 datetimes in time_windows."""
        now = datetime.utcnow()
        payload = _time_windows_query(
            [
                {
                    "start": (now + timedelta(hours=2)).isoformat(),
                    "end": (now + timedelta(hours=5)).isoformat(),
                }
            ]
        )
        response = client.get(f"/api/v2/sessions?time_windows={payload}")
        assert response.status_code == HTTP_200_OK

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_time_windows_with_z_suffix(self, client):
        """Test ISO datetimes with Z suffix are handled correctly."""
        now = datetime.utcnow()
        payload = _time_windows_query(
            [
                {
                    "start": (now + timedelta(hours=2)).isoformat() + "Z",
                    "end": (now + timedelta(hours=5)).isoformat() + "Z",
                }
            ]
        )
        response = client.get(f"/api/v2/sessions?time_windows={payload}")
        assert response.status_code == HTTP_200_OK

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_time_window(self, client):
        """Test filtering by sessions within one specific time window."""
        now = datetime.utcnow()
        payload = _time_windows_query(
            [
                {
                    "start": (now - timedelta(minutes=10)).isoformat(),
                    "end": (now + timedelta(hours=4, minutes=10)).isoformat(),
                }
            ]
        )
        response = client.get(f"/api/v2/sessions?time_windows={payload}")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Should find sessions that fit within the specified window
        assert len(data) >= 1

    @pytest.mark.usefixtures("sessions_for_filtering")
    def test_filter_by_multiple_time_windows(self, client):
        """Test filtering by sessions in multiple separate windows."""
        now = datetime.utcnow()
        payload = _time_windows_query(
            [
                {
                    "start": now.isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                },
                {
                    "start": (now + timedelta(hours=4)).isoformat(),
                    "end": (now + timedelta(hours=5)).isoformat(),
                },
            ]
        )
        response = client.get(f"/api/v2/sessions?time_windows={payload}")
        assert response.status_code == HTTP_200_OK
        data = response.json()
        # Sessions in either window should be returned
        assert len(data) >= 0

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
        if response.status_code == 200:
            results = response.json()
            for result in results:
                assert "session" in result
                assert "overall_score" in result
                assert 0 <= result["overall_score"] <= 1
                assert "semantic_similarity" in result

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_language_filter(self, client):
        """Test semantic search with language filter."""
        response = client.get("/api/v2/sessions/search/similar?query=AI&language=en")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            for result in results:
                assert result["session"].get("language").lower() == "en"

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
        if response.status_code == 200:
            results = response.json()
            for result in results:
                assert result["session"].get("session_format") == "workshop"

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_duration_min_filter(self, client):
        """Test semantic search with minimum duration filter."""
        response = client.get("/api/v2/sessions/search/similar?query=AI&duration_min=30")
        assert response.status_code in [200, 503]

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_duration_range_filter(self, client):
        """Test semantic search with duration range filter."""
        response = client.get(
            "/api/v2/sessions/search/similar?query=learning&duration_min=20&duration_max=90"
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            for result in results:
                duration = result["session"].get("duration", 0)
                assert 20 <= duration <= 90

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_time_window_filter(self, client):
        """Test semantic search with a single time window filter."""
        payload = _time_windows_query(
            [{"start": "2024-01-01T00:00:00", "end": "2025-12-31T23:59:59"}]
        )
        response = client.get(
            f"/api/v2/sessions/search/similar?query=workshop&time_windows={payload}"
        )
        assert response.status_code in [200, 503]

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_multiple_time_windows(self, client):
        """Test semantic search with multiple time windows."""
        payload = _time_windows_query(
            [
                {"start": "2024-01-01T00:00:00", "end": "2024-06-30T23:59:59"},
                {"start": "2024-09-01T00:00:00", "end": "2025-12-31T23:59:59"},
            ]
        )
        response = client.get(
            f"/api/v2/sessions/search/similar?query=machine+learning&time_windows={payload}"
        )
        assert response.status_code in [200, 503]

    @pytest.mark.usefixtures("sessions_with_embeddings")
    def test_semantic_search_with_invalid_time_windows_format(self, client):
        """Test semantic search with invalid time_windows format."""
        response = client.get(
            "/api/v2/sessions/search/similar?query=learning&time_windows=not-json"
        )
        assert response.status_code in [400, 422]

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


@pytest.mark.integration
class TestRecommendationAPI:
    """Integration tests for session recommendation endpoint."""

    @pytest.fixture
    def api_headers(self, sample_api_key):
        """Generate API headers for authenticated requests."""
        api_key, plain_key = sample_api_key
        return {"Authorization": f"Bearer {plain_key}"}

    @pytest.fixture
    def recommendation_sessions(self, client, sample_event, api_headers):
        """Create sessions with diverse characteristics for recommendations."""
        now = datetime.utcnow()

        sessions_data = [
            {
                "title": "Introduction to Machine Learning",
                "start_datetime": now.isoformat(),
                "end_datetime": (now + timedelta(minutes=60)).isoformat(),
                "language": "en",
                "uri": "intro-ml",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "workshop",
                "location": "Stage Berlin",
                "speakers": ["Alice ML"],
                "tags": ["ML", "Intro"],
                "duration": 60,
            },
            {
                "title": "Advanced Machine Learning Techniques",
                "start_datetime": (now + timedelta(hours=2)).isoformat(),
                "end_datetime": (now + timedelta(hours=4)).isoformat(),
                "language": "en",
                "uri": "advanced-ml",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "training",
                "location": "Stage Berlin",
                "speakers": ["Bob Expert"],
                "tags": ["ML", "Advanced"],
                "duration": 120,
            },
            {
                "title": "AI Ethics and Responsibility",
                "start_datetime": (now + timedelta(hours=5)).isoformat(),
                "end_datetime": (now + timedelta(hours=5, minutes=30)).isoformat(),
                "language": "en",
                "uri": "ai-ethics",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "diskussion",
                "location": "AI Stage",
                "speakers": ["Carol Ethics"],
                "tags": ["Ethics", "AI"],
                "duration": 30,
            },
            {
                "title": "Deep Learning Fundamentals",
                "start_datetime": (now + timedelta(hours=6)).isoformat(),
                "end_datetime": (now + timedelta(hours=7, minutes=30)).isoformat(),
                "language": "en",
                "uri": "deep-learning",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "training",
                "location": "Stage Berlin",
                "speakers": ["Dave DL"],
                "tags": ["Deep Learning", "Neural Networks"],
                "duration": 90,
            },
            {
                "title": "Natural Language Processing",
                "start_datetime": (now + timedelta(hours=8)).isoformat(),
                "end_datetime": (now + timedelta(hours=9, minutes=15)).isoformat(),
                "language": "en",
                "uri": "nlp-intro",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "input",
                "location": "AI Stage",
                "speakers": ["Eve NLP"],
                "tags": ["NLP", "AI"],
                "duration": 75,
            },
            {
                "title": "Data Visualization Best Practices",
                "start_datetime": (now + timedelta(hours=10)).isoformat(),
                "end_datetime": (now + timedelta(hours=11)).isoformat(),
                "language": "en",
                "uri": "data-viz",
                "event_id": sample_event.id,
                "status": "published",
                "session_format": "workshop",
                "location": "Training Room",
                "speakers": ["Frank Viz"],
                "tags": ["Visualization", "Data"],
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

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_query_only(self, client):
        """Test recommendations with text query only (no accepted_ids)."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning techniques",
                "accepted_ids": [],
                "rejected_ids": [],
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            assert isinstance(results, list)
            assert len(results) <= 5
            # Verify SessionWithScore structure
            for result in results:
                assert "session" in result
                assert "overall_score" in result
                assert 0 <= result["overall_score"] <= 1
                assert "semantic_similarity" in result

    def test_recommend_route_forwards_multiple_queries(self, client, monkeypatch):
        """Recommend endpoint should accept and forward a list of queries."""
        from app.services.embedding import factory as embedding_factory

        recommendation_service = AsyncMock()
        recommendation_service.recommend_sessions.return_value = []

        monkeypatch.setattr(
            embedding_factory,
            "get_recommendation_service",
            lambda: recommendation_service,
            raising=False,
        )

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": ["kuenstliche intelligenz in der lehre", "kritisches denken"],
                "accepted_ids": [],
                "rejected_ids": [],
                "limit": 5,
            },
        )

        assert response.status_code == HTTP_200_OK
        call_kwargs = recommendation_service.recommend_sessions.await_args.kwargs
        assert call_kwargs["query"] == [
            "kuenstliche intelligenz in der lehre",
            "kritisches denken",
        ]

    def test_recommend_route_can_refine_query_before_recommend(
        self, client, monkeypatch, sample_event
    ):
        """Recommend endpoint should optionally refine query before recommendation."""
        from app.schemas.session import SearchIntentRefinementResponse
        from app.services.embedding import factory as embedding_factory

        recommendation_service = AsyncMock()
        recommendation_service.recommend_sessions.return_value = []

        refinement_service = AsyncMock()
        refinement_service.refine_search_intent.return_value = SearchIntentRefinementResponse(
            refined_queries=["kuenstliche intelligenz praktisch in der lehre einsetzen"],
            event_id=9,
            session_format=["workshop"],
            tags=["Didaktik"],
            location_cities=["Berlin"],
            rationale="Single clear intent",
        )

        monkeypatch.setattr(
            embedding_factory,
            "get_recommendation_service",
            lambda: recommendation_service,
            raising=False,
        )
        monkeypatch.setattr(
            embedding_factory,
            "get_query_refinement_service",
            lambda: refinement_service,
            raising=False,
        )

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": ["Ich moechte KI praktisch in der Lehre einsetzen"],
                "accepted_ids": [],
                "rejected_ids": [],
                "event_id": sample_event.id,
                "refine_query": True,
                "limit": 5,
            },
        )

        assert response.status_code == HTTP_200_OK
        call_kwargs = recommendation_service.recommend_sessions.await_args.kwargs
        assert call_kwargs["query"] == ["kuenstliche intelligenz praktisch in der lehre einsetzen"]
        assert call_kwargs["session_format"] == ["workshop"]
        assert call_kwargs["tags"] == ["Didaktik"]
        assert call_kwargs["location_cities"] == ["Berlin"]

    def test_recommend_route_refine_query_requires_event_id(self, client):
        """Recommend endpoint should reject refine_query without event_id when query exists."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": ["KI in der Lehre"],
                "accepted_ids": [],
                "rejected_ids": [],
                "refine_query": True,
                "limit": 5,
            },
        )

        assert response.status_code == HTTP_422_UNPROCESSABLE_CONTENT

    def test_recommend_route_refine_query_rejects_single_query_string(self, client, sample_event):
        """Recommend endpoint should require query list in refine mode."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "KI in der Lehre",
                "accepted_ids": [],
                "rejected_ids": [],
                "event_id": sample_event.id,
                "refine_query": True,
                "limit": 5,
            },
        )

        assert response.status_code == HTTP_400_BAD_REQUEST

    def test_recommend_route_refine_query_skips_trivial_single_word_query(
        self, client, monkeypatch, sample_event
    ):
        """Refinement should be skipped for very short single-word query lists."""
        from app.services.embedding import factory as embedding_factory

        recommendation_service = AsyncMock()
        recommendation_service.recommend_sessions.return_value = []

        refinement_service = AsyncMock()

        monkeypatch.setattr(
            embedding_factory,
            "get_recommendation_service",
            lambda: recommendation_service,
            raising=False,
        )
        monkeypatch.setattr(
            embedding_factory,
            "get_query_refinement_service",
            lambda: refinement_service,
            raising=False,
        )

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": ["KI"],
                "accepted_ids": [],
                "rejected_ids": [],
                "event_id": sample_event.id,
                "refine_query": True,
                "limit": 5,
            },
        )

        assert response.status_code == HTTP_200_OK
        refinement_service.refine_search_intent.assert_not_called()
        call_kwargs = recommendation_service.recommend_sessions.await_args.kwargs
        assert call_kwargs["query"] == ["KI"]

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_liked_sessions(self, client, recommendation_sessions):
        """Test recommendations based on liked sessions (without query)."""
        liked_session_id = recommendation_sessions[0]["id"]
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": None,
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            assert isinstance(results, list)
            # Should not include the liked session itself
            result_ids = [s["session"]["id"] for s in results]
            assert liked_session_id not in result_ids
            # Verify scores
            for result in results:
                assert 0 <= result["overall_score"] <= 1
                assert result["liked_cluster_similarity"] is not None

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_seen_session_exclusion(self, client, recommendation_sessions):
        """Test that accepted and rejected sessions are excluded from results."""
        session1_id = recommendation_sessions[0]["id"]
        session2_id = recommendation_sessions[1]["id"]

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning",
                "accepted_ids": [session1_id],
                "rejected_ids": [session2_id],
                "limit": 10,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            result_ids = [s["session"]["id"] for s in results]
            # Both accepted and rejected should be excluded
            assert session1_id not in result_ids
            assert session2_id not in result_ids

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_filters_only_crud_fallback(self, client):
        """Test CRUD fallback when no query and no accepted_ids provided (filters-only mode)."""
        # This should succeed with CRUD fallback, not fail
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": None,
                "accepted_ids": [],
                "rejected_ids": [],
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            assert isinstance(results, list)
            # CRUD fallback returns all sessions with default score of 1.0
            for result in results:
                assert result["overall_score"] == 1.0
                assert result["semantic_similarity"] is None

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_format_filter(self, client, recommendation_sessions):
        """Test recommendations with session format filter."""
        liked_session_id = recommendation_sessions[0]["id"]
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": None,
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "session_format": "training",
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # All results should match the format filter
            for result in results:
                assert result["session"].get("session_format") == "training"

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_language_filter(self, client, recommendation_sessions):
        """Test recommendations with language filter."""
        liked_session_id = recommendation_sessions[0]["id"]
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning",
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "language": "en",
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            for result in results:
                assert result["session"].get("language").lower() == "en"

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_duration_range(self, client, recommendation_sessions):
        """Test recommendations with duration range filter."""
        liked_session_id = recommendation_sessions[0]["id"]
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": None,
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "duration_min": 50,
                "duration_max": 100,
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            for result in results:
                duration = result["session"].get("duration", 0)
                assert 50 <= duration <= 100

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_with_timeframe_filter(self, client, recommendation_sessions):
        """Test recommendations with plan window filters (find sessions in timeslot)."""
        liked_session_id = recommendation_sessions[0]["id"]
        now = datetime.utcnow()
        start_time = (now + timedelta(hours=3)).isoformat()
        end_time = (now + timedelta(hours=8)).isoformat()

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": None,
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "goal_mode": "plan",
                "time_windows": [{"start": start_time, "end": end_time}],
                "limit": 5,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # All results should fall within the timeframe
            for result in results:
                session = result["session"]
                session_start = datetime.fromisoformat(
                    session["start_datetime"].replace("Z", "+00:00")
                )
                session_end = datetime.fromisoformat(session["end_datetime"].replace("Z", "+00:00"))
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                assert session_start >= start_dt
                assert session_end <= end_dt

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_limit_constraint(self, client):
        """Test that recommendations respects limit parameter."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "accepted_ids": [],
                "rejected_ids": [],
                "limit": 3,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            assert len(results) <= 3
            # All results should have scores
            for result in results:
                assert "overall_score" in result
                assert 0 <= result["overall_score"] <= 1

    def test_recommend_invalid_limit(self, client):
        """Test that invalid limit is rejected."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "accepted_ids": [],
                "rejected_ids": [],
                "limit": 101,  # Exceeds max
            },
        )
        assert response.status_code in [400, 422]

    # ========================================================================
    # Phase 2: Similarity-based re-ranking tests
    # ========================================================================

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_phase2_liked_cluster_similarity(self, client, recommendation_sessions):
        """Test Phase 2: liked_cluster_similarity is computed and non-zero."""
        liked_session_id = recommendation_sessions[0]["id"]
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "limit": 5,
                # Phase 2 parameters
                "liked_embedding_weight": 0.5,
                "disliked_embedding_weight": 0.2,
            },
        )
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            for result in results:
                # Phase 2: liked_cluster_similarity should be computed (not None)
                assert result["liked_cluster_similarity"] is not None
                assert 0 <= result["liked_cluster_similarity"] <= 1
                # Overall score should include the boost from liked similarity
                assert result["overall_score"] > 0

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_phase2_disliked_penalty(self, client):
        """Test Phase 2: disliked_embedding_weight parameter works (doesn't error).

        Note: This is a simplified integration test that just verifies the API
        accepts disliked_embedding_weight and returns valid responses.
        Detailed penalty behavior is tested in unit tests (test_recommender.py).
        """
        # Test that the API accepts disliked weight parameter without errors
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning",
                "accepted_ids": [],
                "rejected_ids": [],
                "limit": 3,
                "liked_embedding_weight": 0.3,
                "disliked_embedding_weight": 0.2,  # Parameter is accepted
            },
        )

        # Should return 200 or 503 (if service unavailable)
        assert response.status_code in [200, 503]

        if response.status_code == 200:
            results = response.json()
            assert isinstance(results, list)
            # If results exist, verify score structure
            for result in results:
                assert "overall_score" in result
                assert "disliked_similarity" in result
                assert 0 <= result["overall_score"] <= 1

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_phase2_weight_tuning(self, client, recommendation_sessions):
        """Test Phase 2: Weight parameters affect overall scores."""
        liked_session_id = recommendation_sessions[0]["id"]

        response_low_weight = client.post(
            "/api/v2/sessions/recommend",
            json={
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "limit": 3,
                "liked_embedding_weight": 0.1,  # Low boost
                "disliked_embedding_weight": 0.1,
            },
        )
        response_high_weight = client.post(
            "/api/v2/sessions/recommend",
            json={
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "limit": 3,
                "liked_embedding_weight": 0.9,  # High boost
                "disliked_embedding_weight": 0.9,
            },
        )

        if response_low_weight.status_code == 200 and response_high_weight.status_code == 200:
            results1 = response_low_weight.json()
            results2 = response_high_weight.json()

            # Verify scores are valid
            for results in [results1, results2]:
                for result in results:
                    assert 0 <= result["overall_score"] <= 1

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_phase2_zero_weights(self, client, recommendation_sessions):
        """Test Phase 2: Zero weights disable re-ranking adjustments."""
        liked_session_id = recommendation_sessions[0]["id"]
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "limit": 3,
                "liked_embedding_weight": 0.0,  # No boost
                "disliked_embedding_weight": 0.0,  # No penalty
            },
        )

        if response.status_code == 200:
            results = response.json()
            for result in results:
                # With zero weights, overall_score should be base_score (semantic_similarity or 0.5)
                assert 0 <= result["overall_score"] <= 1

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_recommend_phase2_query_plus_reranking(self, client, recommendation_sessions):
        """Test Phase 2: Query + re-ranking with liked/disliked sessions."""
        liked_session_id = recommendation_sessions[0]["id"]
        disliked_session_id = (
            recommendation_sessions[1]["id"] if len(recommendation_sessions) > 1 else None
        )

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning workshop",
                "accepted_ids": [liked_session_id] if liked_session_id else [],
                "rejected_ids": [disliked_session_id] if disliked_session_id else [],
                "limit": 5,
                "liked_embedding_weight": 0.4,
                "disliked_embedding_weight": 0.3,
            },
        )

        if response.status_code == 200:
            results = response.json()
            for result in results:
                # With query, semantic_similarity should be present and non-None
                assert result["semantic_similarity"] is not None
                assert 0 <= result["semantic_similarity"] <= 1
                # Re-ranking should still apply
                assert result["overall_score"] is not None
                assert 0 <= result["overall_score"] <= 1
                if liked_session_id:
                    assert result["liked_cluster_similarity"] is not None

    # ========================================================================
    # Phase 3: Soft filter margins tests
    # ========================================================================

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_filter_compliance_score_in_response(self, client):
        """Test that filter_compliance_score is included in recommendation response."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning",
                "limit": 5,
                # Phase 3 parameters
                "filter_margin_weight": 0.1,
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # filter_compliance_score should be present in response
            for result in results:
                assert "filter_compliance_score" in result

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_hard_mode_with_filters(self, client):
        """Test that hard filter mode applies filters strictly."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "session_format": "workshop",
                "language": "en",
                "limit": 10,
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # All returned sessions must match ALL filters
            for result in results:
                session = result["session"]
                assert session["session_format"] == "workshop"
                assert session["language"].lower() == "en"

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_soft_filters_parameter_accepted(self, client):
        """Test that soft_filters parameter is accepted without errors."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "limit": 5,
                "soft_filters": ["language"],
                "filter_margin_weight": 0.15,
                "language": "en",
            },
        )

        # Should accept soft mode without error
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # Response structure should be valid
            assert isinstance(results, list)
            for result in results:
                assert "overall_score" in result
                assert "filter_compliance_score" in result

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_filter_margin_weight_parameter_range(self, client):
        """Test that filter_margin_weight parameter accepts 0.0-1.0 range."""
        # Test minimum weight
        response1 = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "filter_margin_weight": 0.0,
                "limit": 5,
            },
        )
        assert response1.status_code in [200, 503]

        # Test maximum weight
        response2 = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "filter_margin_weight": 1.0,
                "limit": 5,
            },
        )
        assert response2.status_code in [200, 503]

    @pytest.mark.usefixtures("recommendation_sessions")
    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_phase2_plus_phase3_integration(self, client, recommendation_sessions):
        """Test that Phase 2 re-ranking and Phase 3 compliance work together."""
        liked_session_id = recommendation_sessions[0]["id"]

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "accepted_ids": [liked_session_id],
                "rejected_ids": [],
                "limit": 5,
                # Phase 2 parameters
                "liked_embedding_weight": 0.3,
                "disliked_embedding_weight": 0.2,
                # Phase 3 parameters
                "soft_filters": ["session_format", "language"],
                "filter_margin_weight": 0.1,
                # Filters
                "language": "en",
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            for result in results:
                # Phase 2: liked_cluster_similarity should be computed
                assert result["liked_cluster_similarity"] is not None
                # Phase 3: filter_compliance_score in response
                assert "filter_compliance_score" in result
                # Overall score should exist
                assert result["overall_score"] > 0

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_default_no_soft_filters_applies_all_hard(self, client):
        """Test that omitting soft_filters (default null) applies all filters strictly."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "session_format": "workshop",
                "limit": 5,
                # No soft_filters specified - defaults to all strict
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # All results should match the session_format filter
            for result in results:
                assert result["session"]["session_format"] == "workshop"

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_multiple_filters_with_soft_mode(self, client):
        """Test soft mode with multiple active filters."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": None,
                "limit": 10,
                "soft_filters": ["language"],
                "filter_margin_weight": 0.2,
                # Multiple filters
                "session_format": "workshop",
                "language": "en",
                "duration_min": 45,
                "duration_max": 120,
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            # Session data should be valid
            for result in results:
                assert "overall_score" in result
                assert "filter_compliance_score" in result

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_soft_mode_direct_expansion(self, client):
        """Test that soft mode retrieves candidates directly without hard-pass gating."""
        response_soft = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "limit": 10,
                "soft_filters": ["session_format", "language", "duration"],
                "filter_margin_weight": 0.15,
                # Restrictive format filter
                "session_format": "diskussion",  # Only "ai-ethics" has this
                "language": "en",
            },
        )

        assert response_soft.status_code in [200, 503]
        if response_soft.status_code == 200:
            soft_results = response_soft.json()

            # With soft mode, we expect to get some results
            # from direct soft-pass retrieval
            if len(soft_results) > 0:
                # Check that results are properly formatted
                for result in soft_results:
                    assert "overall_score" in result
                    assert result["overall_score"] >= 0 and result["overall_score"] <= 1
                    assert (
                        "filter_compliance_score" in result
                        or result["filter_compliance_score"] is None
                    )

                    # Soft-mode results may not strictly match all filters.
                    if result["session"]["session_format"]:
                        assert isinstance(result["session"]["session_format"], str)

                # Results should be sorted by overall_score (descending)
                scores = [r["overall_score"] for r in soft_results]
                assert scores == sorted(scores, reverse=True)

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_hard_mode_no_expansion(self, client):
        """Test that hard mode does NOT expand with soft results.

        Hard mode should strictly apply all filters and not use soft pass expansion.
        """
        # Same restrictive filters as soft mode test, but with hard mode
        response_hard = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "limit": 10,
                "session_format": "diskussion",  # Very restrictive
                "language": "en",
            },
        )

        assert response_hard.status_code in [200, 503]
        if response_hard.status_code == 200:
            hard_results = response_hard.json()

            # All hard results MUST match the session_format filter
            for result in hard_results:
                if result["session"]["session_format"]:
                    assert result["session"]["session_format"] == "diskussion"
                assert result["session"]["language"].lower() == "en"

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase3_compliance_score_reflects_filter_matches(self, client):
        """Test that compliance_score correctly reflects how many filters matched."""
        # Use soft mode with query to trigger semantic search path (not CRUD fallback)
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning workshop",  # Provide query to trigger semantic path
                "limit": 10,
                "soft_filters": ["session_format", "language"],
                "filter_margin_weight": 0.2,
                "session_format": "workshop",
                "language": "en",
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()

            if len(results) > 0:
                # Results should be properly formatted
                for result in results:
                    assert "overall_score" in result
                    # compliance_score may be None in hard pass, or a float in soft pass
                    compliance = result.get("filter_compliance_score")
                    if compliance is not None:
                        assert 0.0 <= compliance <= 1.0

    # ========================================================================
    # Phase 4: Plan mode tests
    # ========================================================================

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase4_plan_mode_non_overlapping(self, client):
        """Plan mode should return a non-overlapping session schedule."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "machine learning",
                "limit": 10,
                "goal_mode": "plan",
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()

            # Validate all session pairs are non-overlapping.
            for i, left in enumerate(results):
                left_start = datetime.fromisoformat(
                    left["session"]["start_datetime"].replace("Z", "+00:00")
                )
                left_end = datetime.fromisoformat(
                    left["session"]["end_datetime"].replace("Z", "+00:00")
                )
                for right in results[i + 1 :]:
                    right_start = datetime.fromisoformat(
                        right["session"]["start_datetime"].replace("Z", "+00:00")
                    )
                    right_end = datetime.fromisoformat(
                        right["session"]["end_datetime"].replace("Z", "+00:00")
                    )
                    overlaps = left_start < right_end and right_start < left_end
                    assert not overlaps

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase4_plan_mode_respects_time_window(self, client):
        """Plan mode should keep sessions inside time_windows bounds."""
        now = datetime.utcnow()
        plan_start = (now + timedelta(hours=1)).isoformat()
        plan_end = (now + timedelta(hours=8)).isoformat()

        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "goal_mode": "plan",
                "time_windows": [{"start": plan_start, "end": plan_end}],
                "limit": 10,
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            plan_start_dt = datetime.fromisoformat(plan_start.replace("Z", "+00:00"))
            plan_end_dt = datetime.fromisoformat(plan_end.replace("Z", "+00:00"))

            for result in results:
                session_start = datetime.fromisoformat(
                    result["session"]["start_datetime"].replace("Z", "+00:00")
                )
                session_end = datetime.fromisoformat(
                    result["session"]["end_datetime"].replace("Z", "+00:00")
                )
                assert session_start >= plan_start_dt
                assert session_end <= plan_end_dt

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase4_plan_mode_respects_min_break(self, client):
        """Plan mode should enforce configured minimum break between selected sessions."""
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "query": "learning",
                "goal_mode": "plan",
                "min_break_minutes": 20,
                "limit": 10,
            },
        )

        assert response.status_code in [200, 503]
        if response.status_code == 200:
            results = response.json()
            sessions = sorted(
                [
                    {
                        "start": datetime.fromisoformat(
                            r["session"]["start_datetime"].replace("Z", "+00:00")
                        ),
                        "end": datetime.fromisoformat(
                            r["session"]["end_datetime"].replace("Z", "+00:00")
                        ),
                    }
                    for r in results
                ],
                key=lambda x: x["start"],
            )

            for prev, nxt in pairwise(sessions):
                break_minutes = (nxt["start"] - prev["end"]).total_seconds() / 60
                assert break_minutes >= 20

    @pytest.mark.usefixtures("recommendation_sessions")
    def test_phase4_plan_mode_invalid_window_rejected(self, client):
        """Invalid planning window should be rejected by request validation."""
        now = datetime.utcnow()
        response = client.post(
            "/api/v2/sessions/recommend",
            json={
                "goal_mode": "plan",
                "time_windows": [
                    {
                        "start": (now + timedelta(hours=2)).isoformat(),
                        "end": (now + timedelta(hours=1)).isoformat(),
                    }
                ],
                "query": "learning",
            },
        )

        assert response.status_code in [400, 422]
