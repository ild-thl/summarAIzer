"""Unit tests for LLM-backed query refinement."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.schemas.session import SearchIntentRefinementRequest
from app.services.embedding.query_refinement_service import QueryRefinementService


@pytest.mark.asyncio
class TestQueryRefinementService:
    """Test service behavior around query and filter refinement."""

    async def test_refines_query_and_applies_missing_recommendations(self):
        response = MagicMock(
            refined_queries=["ethischer Einsatz von KI im Unterricht"],
            recommended_session_format=[MagicMock(value="diskussion"), MagicMock(value="workshop")],
            recommended_tags=["Ethik", "Nicht vorhanden", "Bildung"],
            recommended_location=["Raum A", "Nicht vorhanden"],
            rationale="The query expresses a discussion-oriented collaborative learning intent.",
        )
        service = QueryRefinementService(model=MagicMock())
        service.agent = MagicMock(ainvoke=AsyncMock(return_value={"structured_response": response}))
        service._get_event_filter_inventory = MagicMock(
            return_value=(["Ethik", "Bildung"], ["Raum A"])
        )

        result = await service.refine_search_intent(
            MagicMock(spec=Session),
            SearchIntentRefinementRequest(
                queries=[
                    "Ich will mit anderen ueber den ethischen Einsatz von KI im Unterricht diskutieren"
                ],
                event_id=5,
            ),
        )

        assert result.refined_queries == ["ethischer Einsatz von KI im Unterricht"]
        assert result.session_format == ["diskussion", "workshop"]
        assert result.tags == ["Ethik", "Bildung"]
        assert result.location == ["Raum A"]

    async def test_preserves_explicit_user_filters(self):
        response = MagicMock(
            refined_queries=["ethischer Einsatz von KI im Unterricht"],
            recommended_session_format=[MagicMock(value="diskussion")],
            recommended_tags=["Ethik", "Bildung"],
            recommended_location=["Raum B"],
            rationale="The topical intent can be expressed more directly for retrieval.",
        )
        service = QueryRefinementService(model=MagicMock())
        service.agent = MagicMock(ainvoke=AsyncMock(return_value={"structured_response": response}))

        result = await service.refine_search_intent(
            MagicMock(spec=Session),
            SearchIntentRefinementRequest(
                queries=["Ethik in KI im Unterricht"],
                event_id=5,
                session_format=["input"],
                tags=["Bereits gesetzt"],
                location=["Raum C"],
            ),
        )

        assert result.session_format == ["input"]
        assert result.tags == ["Bereits gesetzt"]
        assert result.location == ["Raum C"]

    async def test_multiple_refined_queries_are_returned(self):
        response = MagicMock(
            refined_queries=[
                "Kuenstliche Intelligenz praktisch in der Lehre einsetzen",
                "kritisches Denken und Demokratiebewusstsein schulen",
            ],
            recommended_session_format=[],
            recommended_tags=[],
            recommended_location=[],
            rationale="Two independent topical interests are present.",
        )
        service = QueryRefinementService(model=MagicMock())
        service.agent = MagicMock(ainvoke=AsyncMock(return_value={"structured_response": response}))
        service._get_event_filter_inventory = MagicMock(return_value=([], []))

        result = await service.refine_search_intent(
            MagicMock(spec=Session),
            SearchIntentRefinementRequest(
                queries=[
                    "Kuenstliche Intelligenz praktisch in der Lehre einsetzen und "
                    "kritisches Denken sowie Demokratiebewusstsein schulen"
                ],
                event_id=5,
            ),
        )

        assert result.refined_queries == [
            "Kuenstliche Intelligenz praktisch in der Lehre einsetzen",
            "kritisches Denken und Demokratiebewusstsein schulen",
        ]

    async def test_allowed_session_formats_come_from_enum(self):
        assert QueryRefinementService._get_allowed_session_formats() == [
            "input",
            "lightning talk",
            "diskussion",
            "workshop",
            "training",
        ]

    async def test_system_prompt_includes_available_value_constraints(self):
        prompt = QueryRefinementService._build_system_prompt()

        assert (
            "Allowed session formats: input, lightning talk, diskussion, workshop, training."
            in prompt
        )
        assert "you may only choose values from the provided available lists" in prompt

    async def test_human_payload_omits_duration_and_time_windows(self):
        payload = QueryRefinementService._build_human_payload(
            SearchIntentRefinementRequest(
                queries=["Ich will ueber KI diskutieren"],
                event_id=4,
            ),
            ["Ethik"],
            ["Raum A"],
        )

        assert "language" not in payload["existing_filters"]
        assert "duration_min" not in payload["existing_filters"]
        assert "duration_max" not in payload["existing_filters"]
        assert "time_windows" not in payload["existing_filters"]

    async def test_inventory_lookup_uses_cache(self):
        """Test event filter inventory is only loaded once within the TTL."""
        service = QueryRefinementService(model=MagicMock())
        db = MagicMock(spec=Session)
        original_getter = session_crud.get_available_tags_and_locations

        try:
            session_crud.get_available_tags_and_locations = MagicMock(
                return_value=(["Ethik"], ["Raum A"])
            )

            first = service._get_event_filter_inventory(db, 3)
            second = service._get_event_filter_inventory(db, 3)

            assert first == second == (["Ethik"], ["Raum A"])
            session_crud.get_available_tags_and_locations.assert_called_once_with(db, 3)
        finally:
            session_crud.get_available_tags_and_locations = original_getter

    async def test_invalidate_event_filter_inventory_removes_cached_entry(self):
        service = QueryRefinementService(model=MagicMock())
        service._event_filter_inventory_cache[3] = MagicMock()

        service.invalidate_event_filter_inventory(3)

        assert 3 not in service._event_filter_inventory_cache
