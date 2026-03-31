"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.database.models import EventStatus, SessionFormat, SessionStatus

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
    location: str | None = Field(None, max_length=255, description="Session location")
    start_datetime: datetime = Field(..., description="Session start datetime")
    end_datetime: datetime = Field(..., description="Session end datetime")
    recording_url: HttpUrl | None = Field(None, description="Recording URL")
    status: SessionStatus = Field(default=SessionStatus.DRAFT, description="Session status")
    session_format: SessionFormat | None = Field(None, description="Session format type")
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
        if v is None:
            return v
        # If it's already an enum, get its value
        if isinstance(v, SessionFormat):
            return v.value
        # Convert to string and lowercase
        v_str = str(v).lower()
        # Validate it matches one of the enum values
        valid_formats = {fmt.value for fmt in SessionFormat}
        if v_str not in valid_formats:
            raise ValueError(
                f"Invalid session_format: {v}. Must be one of: {', '.join(sorted(valid_formats))}"
            )
        return v_str

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
    location: str | None = Field(None, max_length=255)
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
        if v is None:
            return v
        # If it's already an enum, get its value
        if isinstance(v, SessionFormat):
            return v.value
        # Convert to string and lowercase
        v_str = str(v).lower()
        # Validate it matches one of the enum values
        valid_formats = {fmt.value for fmt in SessionFormat}
        if v_str not in valid_formats:
            raise ValueError(
                f"Invalid session_format: {v}. Must be one of: {', '.join(sorted(valid_formats))}"
            )
        return v_str

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
    available_content: list[str] = Field(
        default_factory=list,
        alias="available_content_identifiers",
        description="List of available content identifiers",
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


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

    query: str | None = Field(
        None,
        max_length=8000,
        description="Optional text query for semantic search",
    )
    accepted_ids: list[int] = Field(
        default_factory=list,
        description="Session IDs the user has liked (for centroid-based or query refinement)",
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
    location: list[str] | None = Field(None, description="Filter by location (OR logic)")
    language: list[str] | None = Field(
        None, description="Filter by language codes (OR logic, ISO 639-1)"
    )
    duration_min: int | None = Field(None, ge=0, description="Minimum duration in minutes")
    duration_max: int | None = Field(None, ge=0, description="Maximum duration in minutes")

    # Phase 2: Tuning parameters for re-ranking
    liked_embedding_weight: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Weight (a) to boost sessions similar to liked sessions (0-1). "
        "Higher = more influence from liked session similarities. Default 0.3",
    )
    disliked_embedding_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight (b) to penalize sessions similar to disliked sessions (0-1). "
        "Higher = stronger penalty from disliked similarities. Default 0.2",
    )

    # Phase 3: Soft filter margins
    filter_mode: Literal["hard", "soft"] = Field(
        default="soft",
        description="Filter enforcement mode: 'hard' = strictly match all filters, "
        "'soft' = use soft candidate retrieval and score by filter compliance",
    )
    filter_margin_weight: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="When filter_mode='soft', weight to blend filter_compliance_score into overall_score (0-1). "
        "Default 0.1 means filter compliance contributes 10% to final score. Set to 0.0 if using soft "
        "mode only to expand candidate pool (compliance shown in response but not scored).",
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
        description="Multiplier for candidate pool size before plan optimization (limit * multiplier)",
    )

    # Phase 3.5: Diversity optimization
    diversity_weight: float = Field(
        default=0.0,
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
        if v is None:
            return v
        if isinstance(v, str):
            return [v.lower()]
        return [str(code).lower() for code in v] if v else None

    @model_validator(mode="after")
    def validate_time_window(self):
        """Validate plan-mode specific constraints."""
        if (
            self.goal_mode == "plan"
            and self.time_windows is not None
            and len(self.time_windows) == 0
        ):
            raise ValueError("time_windows must not be empty when provided")
        return self


class SessionWithScore(BaseModel):
    """Session response with recommendation/search metrics."""

    session: SessionResponse
    overall_score: float = Field(..., ge=0, le=1, description="Overall recommendation score (0-1)")
    semantic_similarity: float | None = Field(
        None, ge=0, le=1, description="Semantic similarity to query (0-1, None if no query)"
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
