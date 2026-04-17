"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.database.models import EventStatus, SessionFormat, SessionStatus

# ============================================================================
# Location Schemas
# ============================================================================


class SessionLocationCreate(BaseModel):
    """Schema for creating or updating a session location."""

    city: str | None = Field(None, max_length=255, description="City name (primary filter key)")
    name: str | None = Field(
        None, max_length=255, description="Display name (stage, room, venue, etc.)"
    )
    country: str | None = Field(None, max_length=100, description="Country")
    address: str | None = Field(None, description="Street address")
    postal_code: str | None = Field(None, max_length=20, description="Postal code")


class SessionLocationResponse(BaseModel):
    """Schema for location in session responses."""

    city: str | None = None
    name: str | None = None
    country: str | None = None
    address: str | None = None
    postal_code: str | None = None

    model_config = ConfigDict(from_attributes=True)


def _normalize_session_format_list(
    value: list[str] | str | list[SessionFormat] | SessionFormat | None,
) -> list[str] | None:
    """Normalize session format filters to validated lowercase strings."""
    if value is None:
        return None

    items = value if isinstance(value, list) else [value]
    valid_formats = {fmt.value for fmt in SessionFormat}
    normalized: list[str] = []
    for item in items:
        item_value = item.value if isinstance(item, SessionFormat) else str(item).strip().lower()
        if not item_value:
            continue
        if item_value not in valid_formats:
            raise ValueError(
                f"Invalid session_format: {item}. Must be one of: {', '.join(sorted(valid_formats))}"
            )
        if item_value not in normalized:
            normalized.append(item_value)
    return normalized or None


def _normalize_string_list(
    value: list[str] | str | None,
    *,
    lowercase: bool = False,
) -> list[str] | None:
    """Normalize optional string-list filters by trimming and de-duplicating values."""
    if value is None:
        return None

    items = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if lowercase:
            text = text.lower()
        if text not in normalized:
            normalized.append(text)
    return normalized or None


# ============================================================================
# Event Schemas
# ============================================================================


class EventBase(BaseModel):
    """Base schema for Event with common fields."""

    title: str = Field(..., min_length=1, max_length=255, description="Event title")
    description: str | None = Field(None, max_length=5000, description="Event description")
    start_date: datetime = Field(..., description="Event start datetime")
    end_date: datetime = Field(..., description="Event end datetime")
    location: str | None = Field(None, max_length=255, description="Event location")
    status: EventStatus = Field(default=EventStatus.DRAFT, description="Event status")
    uri: str = Field(..., min_length=1, max_length=255, description="URL-safe identifier")

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str) -> str:
        """Validate URI is URL-safe."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("URI must be alphanumeric with hyphens or underscores only")
        return v.lower()

    @field_validator("end_date")
    @classmethod
    def validate_end_date(cls, v: datetime, info) -> datetime:
        """Ensure end_date is after start_date."""
        if "start_date" in info.data and v <= info.data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v


class EventCreate(EventBase):
    """Schema for creating an Event."""

    pass


class EventUpdate(BaseModel):
    """Schema for updating an Event."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_date: datetime | None = None
    end_date: datetime | None = None
    location: str | None = Field(None, max_length=255)
    status: EventStatus | None = None
    uri: str | None = Field(None, min_length=1, max_length=255)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str | None) -> str | None:
        """Validate URI is URL-safe."""
        if v is not None and not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("URI must be alphanumeric with hyphens or underscores only")
        return v.lower() if v else v


class EventResponse(EventBase):
    """Schema for Event response."""

    id: int = Field(..., description="Event ID")
    owner_id: int | None = Field(None, description="Event owner ID")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Session Schemas
# ============================================================================


class SessionBase(BaseModel):
    """Base schema for Session with common fields."""

    title: str = Field(..., min_length=1, max_length=255, description="Session title")
    speakers: list[str] | None = Field(default=None, description="List of speaker names")
    tags: list[str] | None = Field(default=None, description="Session tags")
    short_description: str | None = Field(None, description="Short description")
    location: SessionLocationCreate | None = Field(None, description="Structured session location")
    start_datetime: datetime = Field(..., description="Session start datetime")
    end_datetime: datetime = Field(..., description="Session end datetime")
    recording_url: HttpUrl | None = Field(None, description="Recording URL")
    status: SessionStatus = Field(default=SessionStatus.DRAFT, description="Session status")
    session_format: SessionFormat = Field(
        default=SessionFormat.OTHER, description="Session format type"
    )
    duration: int | None = Field(None, ge=0, le=1440, description="Duration in minutes")
    language: str = Field(
        default="en", min_length=2, max_length=10, description="ISO 639-1 language code"
    )
    uri: str = Field(..., min_length=1, max_length=255, description="URL-safe identifier")
    event_id: int | None = Field(None, description="Associated event ID")

    @field_validator("session_format", mode="before")
    @classmethod
    def normalize_session_format(cls, v: str | SessionFormat | None) -> str | None:
        """Normalize session_format to lowercase for case-insensitive matching."""
        normalized = _normalize_session_format_list(v)
        return normalized[0] if normalized else None

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(
        cls, v: SessionLocationCreate | dict | str | None
    ) -> SessionLocationCreate | dict | None:
        """Allow legacy string locations by mapping them to the structured location name."""
        if v is None:
            return None
        if isinstance(v, str):
            text = v.strip()
            return {"name": text} if text else None
        return v

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, v: str | None) -> str | None:
        """Normalize language code to lowercase for consistency."""
        if v is None:
            return v
        return str(v).lower()

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str) -> str:
        """Validate URI is URL-safe."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("URI must be alphanumeric with hyphens or underscores only")
        return v.lower()

    @field_validator("end_datetime")
    @classmethod
    def validate_end_datetime(cls, v: datetime, info) -> datetime:
        """Ensure end_datetime is after start_datetime."""
        if "start_datetime" in info.data and v <= info.data["start_datetime"]:
            raise ValueError("end_datetime must be after start_datetime")
        return v

    @field_validator("duration", mode="after")
    @classmethod
    def validate_duration(cls, v: int | None, info) -> int | None:
        """Validate duration matches time difference if both provided."""
        if v is not None and "start_datetime" in info.data and "end_datetime" in info.data:
            calculated_duration = int(
                (info.data["end_datetime"] - info.data["start_datetime"]).total_seconds() / 60
            )

            # Allow some tolerance for rounding
            if v != calculated_duration and abs(v - calculated_duration) > 5:
                raise ValueError(f"Duration should be approximately {calculated_duration} minutes")
        return v


class SessionCreate(SessionBase):
    """Schema for creating a Session."""

    pass


class SessionUpdate(BaseModel):
    """Schema for updating a Session."""

    title: str | None = Field(None, min_length=1, max_length=255)
    speakers: list[str] | None = None
    tags: list[str] | None = None
    short_description: str | None = Field(None)
    location: SessionLocationCreate | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    recording_url: HttpUrl | None = None
    status: SessionStatus | None = None
    session_format: SessionFormat | None = None
    duration: int | None = Field(None, ge=0, le=1440)
    language: str | None = Field(None, min_length=2, max_length=10)
    uri: str | None = Field(None, min_length=1, max_length=255)
    event_id: int | None = None

    @field_validator("session_format", mode="before")
    @classmethod
    def normalize_session_format(cls, v: str | SessionFormat | None) -> str | None:
        """Normalize session_format to lowercase for case-insensitive matching."""
        normalized = _normalize_session_format_list(v)
        return normalized[0] if normalized else None

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(
        cls, v: SessionLocationCreate | dict | str | None
    ) -> SessionLocationCreate | dict | None:
        """Allow legacy string locations by mapping them to the structured location name."""
        if v is None:
            return None
        if isinstance(v, str):
            text = v.strip()
            return {"name": text} if text else None
        return v

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, v: str | None) -> str | None:
        """Normalize language code to lowercase for consistency."""
        if v is None:
            return v
        return str(v).lower()

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str | None) -> str | None:
        """Validate URI is URL-safe."""
        if v is not None and not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("URI must be alphanumeric with hyphens or underscores only")
        return v.lower() if v else v


class SessionResponse(SessionBase):
    """Schema for Session response."""

    id: int = Field(..., description="Session ID")
    owner_id: int | None = Field(None, description="Session owner ID")
    location: SessionLocationResponse | None = Field(
        None, description="Structured session location"
    )
    available_content: list[str] = Field(
        default_factory=list,
        alias="available_content_identifiers",
        description="List of available content identifiers",
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def extract_location_rel(cls, data: object) -> object:
        """Map ORM location_rel -> location for serialization."""
        if hasattr(data, "location_rel"):
            obj = dict(data.__dict__) if hasattr(data, "__dict__") else data
            obj["location"] = data.location_rel
            return obj
        return data


class SessionListResponse(BaseModel):
    """
    Minimal response for list/search/recommend endpoints.

    Only includes fields relevant for session discovery and browsing:
    - No owner_id (privacy)
    - No available_content_identifiers (implementation detail)
    - No created_at/updated_at (admin only)
    - Truncated short_description (200 chars max)
    """

    id: int = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    speakers: list[str] | None = Field(default=None, description="List of speaker names")
    tags: list[str] | None = Field(default=None, description="Session tags")
    short_description: str | None = Field(
        None, description="Short description (truncated to 200 chars)"
    )
    location: SessionLocationResponse | None = Field(
        None, description="Structured session location"
    )
    start_datetime: datetime = Field(..., description="Session start datetime")
    end_datetime: datetime = Field(..., description="Session end datetime")
    recording_url: HttpUrl | None = Field(None, description="Recording URL")
    status: str = Field(..., description="Session status")
    session_format: str | None = Field(None, description="Session format type")
    duration: int | None = Field(None, description="Duration in minutes")
    language: str = Field(default="en", description="ISO 639-1 language code")
    uri: str = Field(..., description="URL-safe identifier")
    event_id: int | None = Field(None, description="Associated event ID")

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def extract_location_rel(cls, data: object) -> object:
        """Map ORM location_rel -> location for serialization."""
        if hasattr(data, "location_rel"):
            obj = dict(data.__dict__) if hasattr(data, "__dict__") else data
            obj["location"] = data.location_rel
            return obj
        return data

    @model_validator(mode="after")
    def truncate_description(self):
        """Truncate short_description to 200 chars for bandwidth efficiency."""
        if self.short_description and len(self.short_description) > 200:
            self.short_description = self.short_description[:200] + "…"
        return self


class SessionWithEvent(SessionResponse):
    """Schema for Session response with associated event."""

    event: EventResponse | None = None


class TimeWindow(BaseModel):
    """Single time window with inclusive bounds."""

    start: datetime = Field(..., description="Window start datetime (ISO 8601)")
    end: datetime = Field(..., description="Window end datetime (ISO 8601)")

    @model_validator(mode="after")
    def validate_window_order(self):
        """Ensure each window has valid ordering."""
        if self.end <= self.start:
            raise ValueError("time window end must be after start")
        return self


class RecommendRequest(BaseModel):
    """Request schema for session recommendations.

    All parameters are optional. Behavior depends on inputs:
    - With query: Semantic search + optional liked/disliked refinement
    - With accepted_ids: Centroid-based recommendations from liked sessions
    - With only filters: Falls back to CRUD list_with_filters (efficient basic filtering)
    - With rejected_ids only: Applies exclusion to basic filtered list
    """

    model_config = ConfigDict(extra="forbid")

    query: str | list[str] | None = Field(
        None,
        max_length=8000,
        description="Optional single query or list of queries for semantic search",
    )
    refine_query: bool = Field(
        default=False,
        description=(
            "If true, run LLM query refinement before recommendation. "
            "Requires event_id when query is provided."
        ),
    )
    accepted_ids: list[int] = Field(
        default_factory=list,
        description="Session IDs the user has liked (for centroid-based or query refinement)",
    )
    exclude_parallel_accepted_sessions: bool = Field(
        default=False,
        description=(
            "If true and time_windows are provided, subtract the time occupied by accepted_ids "
            "from those windows so parallel sessions are excluded from recommendation."
        ),
    )
    rejected_ids: list[int] = Field(
        default_factory=list,
        description="Session IDs to exclude from results",
    )
    limit: int = Field(10, ge=1, le=100, description="Maximum number of recommendations to return")

    # Optional filters (same as search endpoint)
    event_id: int | None = Field(None, description="Filter by event ID")
    session_format: list[str] | None = Field(
        None, description="Filter by session format (OR logic)"
    )
    tags: list[str] | None = Field(None, description="Filter by tags (OR logic)")
    location_cities: list[str] | None = Field(None, description="Filter by city (OR logic)")
    location_names: list[str] | None = Field(
        None, description="Filter by location name such as stage or room (OR logic)"
    )
    language: list[str] | None = Field(
        None, description="Filter by language codes (OR logic, ISO 639-1)"
    )
    duration_min: int | None = Field(None, ge=0, description="Minimum duration in minutes")
    duration_max: int | None = Field(None, ge=0, description="Maximum duration in minutes")

    # Phase 2: Tuning parameters for re-ranking
    liked_embedding_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Weight (a) to boost sessions similar to liked sessions (0-1). "
        "Higher = more influence from liked session similarities. Default 0.3",
    )
    disliked_embedding_weight: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Weight (b) to penalize sessions similar to disliked sessions (0-1). "
        "Higher = stronger penalty from disliked similarities. Default 0.2",
    )
    preference_dominance_margin: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description=(
            "Margin allowed before a disliked similarity is treated as dominant over liked similarity. "
            "Recommendations are excluded only when disliked_similarity exceeds liked_cluster_similarity by more than this margin."
        ),
    )

    # Phase 3: Soft filter margins
    soft_filters: (
        list[
            Literal[
                "session_format",
                "tags",
                "location",
                "language",
                "duration",
                "time_windows",
            ]
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "Optional list of filter attributes to apply as soft scoring rather than strict retrieval. "
            "Attributes listed here are excluded from candidate retrieval and instead scored via "
            "filter compliance (blended into overall_score using filter_margin_weight). "
            "Valid values: 'session_format', 'tags', 'location', 'language', 'duration', 'time_windows'. "
            "Default (null) applies all provided filters strictly."
        ),
    )
    filter_margin_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Weight to blend filter_compliance_score into overall_score when soft_filters are active (0-1). "
            "Default 0.5 means filter compliance contributes 50% to final score."
        ),
    )
    min_overall_score: float | None = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum overall_score required for recommendations. "
            "Set to null to disable thresholding."
        ),
    )
    # Phase 4: Plan optimization mode (non-overlapping schedule curation)
    goal_mode: Literal["similarity", "plan"] = Field(
        default="similarity",
        description="Recommendation goal: 'similarity' (default ranking) or 'plan' (non-overlapping schedule)",
    )
    time_windows: list[TimeWindow] | None = Field(
        None,
        description="Optional time windows used for filtering and plan mode. Sessions must fit entirely inside any window.",
    )
    min_break_minutes: int = Field(
        default=0,
        ge=0,
        le=240,
        description="Minimum break in minutes required between sessions in plan mode",
    )
    max_gap_minutes: int | None = Field(
        default=None,
        ge=0,
        le=720,
        description="Optional maximum allowed gap in minutes between planned sessions",
    )
    plan_candidate_multiplier: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Multiplier for candidate pool oversampling before recommendation selection (limit * multiplier)",
    )

    # Phase 3.5: Diversity optimization
    diversity_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight for diversity re-ranking (0-1). 0 = pure relevance (default), "
        "higher values promote variety in tags, session formats, languages, and embedding space. "
        "Useful when filtering for multiple tags/formats to ensure balanced coverage.",
    )

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize language codes to lowercase for consistency."""
        return _normalize_string_list(v, lowercase=True)

    @field_validator("session_format", mode="before")
    @classmethod
    def normalize_session_format(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize session format filter as validated list."""
        return _normalize_session_format_list(v)

    @field_validator("tags", "location_cities", "location_names", mode="before")
    @classmethod
    def normalize_string_filters(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize optional string-list filters."""
        return _normalize_string_list(v)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, v: str | list[str] | None) -> str | list[str] | None:
        """Normalize query input for single or multi-query requests."""
        if v is None:
            return None

        if isinstance(v, list):
            normalized = _normalize_string_list(v)
            return normalized

        text = str(v).strip()
        return text or None

    @model_validator(mode="after")
    def validate_time_window(self):
        """Validate plan-mode specific constraints."""
        if self.refine_query and self.query and self.event_id is None:
            raise ValueError("event_id is required when refine_query=true and query is provided")
        if (
            self.goal_mode == "plan"
            and self.time_windows is not None
            and len(self.time_windows) == 0
        ):
            raise ValueError("time_windows must not be empty when provided")
        return self


class SearchIntentRefinementRequest(BaseModel):
    """Request payload for LLM-based query and filter refinement."""

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="One or more natural language search intents to optimize for retrieval",
    )
    event_id: int = Field(
        ...,
        ge=1,
        description="Event ID used to constrain suggested tags and locations",
    )
    session_format: list[str] | None = Field(
        None,
        description="Existing session format filters that must be preserved",
    )
    tags: list[str] | None = Field(
        None,
        description="Existing tag filters that must be preserved",
    )
    location_cities: list[str] | None = Field(
        None,
        description="Filter by city (OR logic)",
    )
    location_names: list[str] | None = Field(
        None,
        description="Filter by location name such as stage or room (OR logic)",
    )

    @field_validator("queries", mode="before")
    @classmethod
    def normalize_queries(cls, v: list[str]) -> list[str]:
        """Normalize query list and reject empty/blank input."""
        normalized = _normalize_string_list(v)
        if normalized is None:
            raise ValueError("queries must contain at least one query")
        return normalized[:10]

    @field_validator("session_format", mode="before")
    @classmethod
    def normalize_session_format(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize existing session format filters."""
        return _normalize_session_format_list(v)

    @field_validator("tags", "location_cities", "location_names", mode="before")
    @classmethod
    def normalize_string_filters(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize optional string-list filters."""
        return _normalize_string_list(v)


class SearchIntentRefinementLLMResponse(BaseModel):
    """Structured LLM output for query refinement."""

    refined_queries: list[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description=(
            "One or more refined semantic-search queries focused on content only. "
            "Use multiple queries when the original text contains distinct interests."
        ),
    )
    recommended_session_format: list[SessionFormat] = Field(
        default_factory=list,
        description="Recommended session formats inferred from the query when missing in user filters",
    )
    recommended_tags: list[str] = Field(
        default_factory=list,
        description="Recommended tags inferred from the query when missing in user filters",
    )
    recommended_location_cities: list[str] = Field(
        default_factory=list,
        description="Recommended city filters inferred from the query when missing in user filters",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short explanation of the refinement choices",
    )

    @field_validator("refined_queries", mode="before")
    @classmethod
    def normalize_refined_queries(cls, v: list[str]) -> list[str]:
        """Ensure at least one non-empty refined query is returned."""
        normalized = _normalize_string_list(v)
        if normalized is None:
            raise ValueError("refined_queries must contain at least one query")
        return normalized[:3]

    @field_validator("recommended_tags", mode="before")
    @classmethod
    def normalize_recommended_tags(cls, v: list[str] | str | None) -> list[str]:
        """Normalize recommended tags and remove empty items."""
        normalized = _normalize_string_list(v)
        if normalized is None:
            return []
        return normalized[:8]

    @field_validator("recommended_location_cities", mode="before")
    @classmethod
    def normalize_recommended_location(cls, v: list[str] | str | None) -> list[str]:
        """Normalize recommended city filters and remove empty items."""
        normalized = _normalize_string_list(v)
        if normalized is None:
            return []
        return normalized[:5]

    @field_validator("rationale", mode="before")
    @classmethod
    def normalize_rationale(cls, v: str) -> str:
        """Normalize rationale text."""
        rationale = str(v).strip()
        if not rationale:
            raise ValueError("rationale must not be blank")
        return rationale


class SearchIntentRefinementResponse(BaseModel):
    """API response with refined query and final hard filters."""

    refined_queries: list[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="One or more content-focused queries optimized for semantic retrieval",
    )
    event_id: int | None = Field(None, description="Event ID used for tag/location constraints")
    session_format: list[str] | None = Field(
        None,
        description="Final session format filters after preserving user input and applying safe recommendations",
    )
    tags: list[str] | None = Field(
        None,
        description="Final tag filters after preserving user input and applying safe recommendations",
    )
    location_cities: list[str] | None = Field(
        None, description="Unchanged or recommended city filters"
    )
    rationale: str = Field(..., description="Short explanation of the refinement choices")

    @field_validator("session_format", mode="before")
    @classmethod
    def normalize_session_format(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize response session format filters."""
        return _normalize_session_format_list(v)

    @field_validator("tags", "location_cities", mode="before")
    @classmethod
    def normalize_string_filters(cls, v: list[str] | str | None) -> list[str] | None:
        """Normalize response string-list filters."""
        return _normalize_string_list(v)

    @field_validator("refined_queries", mode="before")
    @classmethod
    def normalize_refined_queries(cls, v: list[str]) -> list[str]:
        """Normalize and validate refined semantic queries."""
        normalized = _normalize_string_list(v)
        if normalized is None:
            raise ValueError("refined_queries must contain at least one query")
        return normalized[:3]


class SessionWithScore(BaseModel):
    """Session response with recommendation/search metrics."""

    session: SessionListResponse
    overall_score: float = Field(..., ge=0, le=1, description="Overall recommendation score (0-1)")
    semantic_similarity: float | None = Field(
        None,
        ge=0,
        le=1,
        description="Semantic similarity to the user query (0-1, None when ranking is driven by liked-session centroid or fallback logic)",
    )
    liked_cluster_similarity: float | None = Field(
        None,
        ge=0,
        le=1,
        description="Similarity to liked sessions cluster (0-1, None if no liked sessions)",
    )
    disliked_similarity: float | None = Field(
        None,
        ge=0,
        le=1,
        description="Similarity to disliked sessions (0-1, None if no disliked sessions)",
    )
    filter_compliance_score: float | None = Field(
        None,
        ge=0,
        le=1,
        description="Phase 3 - Filter compliance for soft-filter margins (0-1, None if hard filter mode)",
    )
    diversity_score: float | None = Field(
        None,
        ge=0,
        le=1,
        description="Phase 3.5 - Diversity contribution score (0-1, None if diversity_weight=0)",
    )
