"""LLM-backed refinement of user search intent for session retrieval."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.crud.session import session_crud
from app.database.models import SessionFormat
from app.schemas.session import (
    SearchIntentRefinementLLMResponse,
    SearchIntentRefinementRequest,
    SearchIntentRefinementResponse,
)
from app.services.embedding.exceptions import QueryRefinementError
from app.workflows.chat_models import ChatModelConfig, create_chat_model

logger = structlog.get_logger()


@dataclass(slots=True)
class EventFilterInventory:
    """Cached event-specific filter inventory."""

    tags: list[str]
    locations: list[str]
    expires_at: datetime


class QueryRefinementService:
    """Uses an LLM to rewrite search queries and suggest safe hard filters."""

    FILTER_INVENTORY_TTL = timedelta(minutes=5)

    def __init__(self, model: BaseChatModel | None = None):
        settings = get_settings()
        if model is None and not settings.llm_model_structured_output:
            raise QueryRefinementError("LLM model configuration is missing")

        self.model = model or create_chat_model(
            ChatModelConfig(
                model=settings.llm_model_structured_output,
                temperature=0.7,
                max_tokens=600,
                top_p=0.8,
            )
        )
        self._event_filter_inventory_cache: dict[int, EventFilterInventory] = {}
        self.agent = create_agent(
            model=self.model,
            response_format=ProviderStrategy(SearchIntentRefinementLLMResponse),
            system_prompt=self._build_system_prompt(),
        )

    @classmethod
    def _build_human_payload(
        cls,
        params: SearchIntentRefinementRequest,
        available_tags: list[str] | None,
        available_locations: list[str] | None,
    ) -> dict[str, Any]:
        """Serialize the current frontend state for the LLM prompt."""
        return {
            "query": params.query,
            "event_id": params.event_id,
            "existing_filters": {
                "session_format": params.session_format,
                "tags": params.tags,
                "location": params.location,
            },
            "allowed_session_formats": cls._get_allowed_session_formats(),
            "available_tags": available_tags,
            "available_locations": available_locations,
        }

    @staticmethod
    def _get_allowed_session_formats() -> list[str]:
        """Return allowed session formats from the canonical enum."""
        return [session_format.value for session_format in SessionFormat]

    @staticmethod
    def _build_session_format_prompt_section() -> str:
        """Build the session-format instruction block."""
        allowed_formats = ", ".join(QueryRefinementService._get_allowed_session_formats())
        return (
            "Recommend session formats only when the query clearly implies a format preference "
            "that should be a hard filter. Allowed session formats: "
            f"{allowed_formats}. "
        )

    @staticmethod
    def _build_tag_location_prompt_section() -> str:
        """Build the tag/location instruction block."""
        return (
            "Recommend tags only when the query contains strong topical cues that are better "
            "represented as hard filters. Recommend locations only when the query clearly expresses "
            "a concrete place preference. For tags and locations, you may only choose values from "
            "the provided available lists. If no valid available value fits, return an empty "
            "recommendation. "
        )

    @classmethod
    def _build_system_prompt(cls) -> str:
        """Assemble the system prompt from smaller instruction blocks."""
        return (
            "You optimize search intent for a festival session retrieval system. "
            "Rewrite the input into content-focused semantic retrieval queries while preserving "
            "the user's actual topics and intent. Remove non-content phrasing like preferences, "
            "meta instructions, and conversational filler. "
            "Split the query into 2 or 3 refined queries when the input contains distinct "
            "interests that should be searched separately. "
            + cls._build_session_format_prompt_section()
            + cls._build_tag_location_prompt_section()
            + "Never replace, broaden, or contradict explicit user filters. If a filter is already "
            "set, leave the corresponding recommendation empty. When uncertain, prefer empty "
            "recommendations."
        )

    @staticmethod
    def _merge_recommended_filters(
        params: SearchIntentRefinementRequest,
        llm_result: SearchIntentRefinementLLMResponse,
        available_tags: list[str] | None,
        available_locations: list[str] | None,
    ) -> tuple[list[str] | None, list[str] | None, list[str] | None]:
        """Keep explicit user filters intact and only fill missing fields safely."""
        session_format = params.session_format or [
            item.value for item in llm_result.recommended_session_format
        ]
        allowed_tags = set(available_tags or [])
        allowed_locations = set(available_locations or [])
        tags = params.tags or [tag for tag in llm_result.recommended_tags if tag in allowed_tags]
        location = params.location or [
            item for item in llm_result.recommended_location if item in allowed_locations
        ]
        return session_format or None, tags or None, location or None

    def clear_inventory_cache(self) -> None:
        """Clear cached event metadata used for tag/location constraints."""
        self._event_filter_inventory_cache.clear()

    def invalidate_event_filter_inventory(self, event_id: int | None) -> None:
        """Invalidate cached tag/location metadata for one event."""
        if event_id is None:
            return
        self._event_filter_inventory_cache.pop(event_id, None)

    def _get_event_filter_inventory(
        self,
        db: Session,
        event_id: int,
    ) -> tuple[list[str] | None, list[str] | None]:
        """Load and cache available tags and locations for an event."""
        cached = self._event_filter_inventory_cache.get(event_id)
        now = datetime.utcnow()
        if cached and cached.expires_at > now:
            return cached.tags, cached.locations

        tags, locations = session_crud.get_available_tags_and_locations(db, event_id)
        self._event_filter_inventory_cache[event_id] = EventFilterInventory(
            tags=tags,
            locations=locations,
            expires_at=now + self.FILTER_INVENTORY_TTL,
        )
        return tags, locations

    async def refine_search_intent(
        self,
        db: Session,
        params: SearchIntentRefinementRequest,
    ) -> SearchIntentRefinementResponse:
        """Refine the free-text query and optionally recommend missing hard filters."""
        available_tags, available_locations = self._get_event_filter_inventory(db, params.event_id)
        messages = [
            HumanMessage(
                content=str(self._build_human_payload(params, available_tags, available_locations))
            ),
        ]

        try:
            result = await self.agent.ainvoke({"messages": messages})
            llm_result = result["structured_response"]
            session_format, tags, location = self._merge_recommended_filters(
                params,
                llm_result,
                available_tags,
                available_locations,
            )

            response = SearchIntentRefinementResponse(
                refined_queries=llm_result.refined_queries,
                event_id=params.event_id,
                session_format=session_format,
                tags=tags,
                location=location,
                rationale=llm_result.rationale,
            )

            logger.info(
                "search_intent_refined",
                query_length=len(params.query),
                session_format_recommended=bool(llm_result.recommended_session_format),
                tags_recommended=bool(tags),
                location_recommended=bool(location),
                event_id=params.event_id,
            )
            return response
        except QueryRefinementError:
            raise
        except Exception as e:
            logger.error(
                "search_intent_refinement_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise QueryRefinementError(f"Search intent refinement failed: {e!s}") from e
