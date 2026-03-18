"""Pydantic schemas for request/response validation."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

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
    short_description: str | None = Field(None, max_length=1000, description="Short description")
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
    short_description: str | None = Field(None, max_length=1000)
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
