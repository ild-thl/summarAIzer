"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl, field_validator, ConfigDict
from app.database.models import SessionFormat, SessionStatus, EventStatus


# ============================================================================
# Event Schemas
# ============================================================================


class EventBase(BaseModel):
    """Base schema for Event with common fields."""

    title: str = Field(..., min_length=1, max_length=255, description="Event title")
    description: Optional[str] = Field(
        None, max_length=5000, description="Event description"
    )
    start_date: datetime = Field(..., description="Event start datetime")
    end_date: datetime = Field(..., description="Event end datetime")
    location: Optional[str] = Field(None, max_length=255, description="Event location")
    status: EventStatus = Field(default=EventStatus.DRAFT, description="Event status")
    uri: str = Field(
        ..., min_length=1, max_length=255, description="URL-safe identifier"
    )

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str) -> str:
        """Validate URI is URL-safe."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "URI must be alphanumeric with hyphens or underscores only"
            )
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

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = Field(None, max_length=255)
    status: Optional[EventStatus] = None
    uri: Optional[str] = Field(None, min_length=1, max_length=255)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: Optional[str]) -> Optional[str]:
        """Validate URI is URL-safe."""
        if v is not None and not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "URI must be alphanumeric with hyphens or underscores only"
            )
        return v.lower() if v else v


class EventResponse(EventBase):
    """Schema for Event response."""

    id: int = Field(..., description="Event ID")
    owner_id: Optional[int] = Field(None, description="Event owner ID")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Session Schemas
# ============================================================================


class SessionBase(BaseModel):
    """Base schema for Session with common fields."""

    title: str = Field(..., min_length=1, max_length=255, description="Session title")
    speakers: Optional[List[str]] = Field(
        default=None, description="List of speaker names"
    )
    categories: Optional[List[str]] = Field(
        default=None, description="Session categories"
    )
    short_description: Optional[str] = Field(
        None, max_length=1000, description="Short description"
    )
    location: Optional[str] = Field(
        None, max_length=255, description="Session location"
    )
    start_datetime: datetime = Field(..., description="Session start datetime")
    end_datetime: datetime = Field(..., description="Session end datetime")
    recording_url: Optional[HttpUrl] = Field(None, description="Recording URL")
    status: SessionStatus = Field(
        default=SessionStatus.DRAFT, description="Session status"
    )
    session_format: Optional[SessionFormat] = Field(
        None, description="Session format type"
    )
    duration: Optional[int] = Field(
        None, ge=0, le=1440, description="Duration in minutes"
    )
    language: str = Field(
        default="en", min_length=2, max_length=10, description="ISO 639-1 language code"
    )
    uri: str = Field(
        ..., min_length=1, max_length=255, description="URL-safe identifier"
    )
    event_id: Optional[int] = Field(None, description="Associated event ID")

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: str) -> str:
        """Validate URI is URL-safe."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "URI must be alphanumeric with hyphens or underscores only"
            )
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
    def validate_duration(cls, v: Optional[int], info) -> Optional[int]:
        """Validate duration matches time difference if both provided."""
        if (
            v is not None
            and "start_datetime" in info.data
            and "end_datetime" in info.data
        ):
            calculated_duration = int(
                (
                    info.data["end_datetime"] - info.data["start_datetime"]
                ).total_seconds()
                / 60
            )
            if v != calculated_duration:
                # Allow some tolerance for rounding
                if abs(v - calculated_duration) > 5:
                    raise ValueError(
                        f"Duration should be approximately {calculated_duration} minutes"
                    )
        return v


class SessionCreate(SessionBase):
    """Schema for creating a Session."""

    pass


class SessionUpdate(BaseModel):
    """Schema for updating a Session."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    speakers: Optional[List[str]] = None
    categories: Optional[List[str]] = None
    short_description: Optional[str] = Field(None, max_length=1000)
    location: Optional[str] = Field(None, max_length=255)
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    recording_url: Optional[HttpUrl] = None
    status: Optional[SessionStatus] = None
    session_format: Optional[SessionFormat] = None
    duration: Optional[int] = Field(None, ge=0, le=1440)
    language: Optional[str] = Field(None, min_length=2, max_length=10)
    uri: Optional[str] = Field(None, min_length=1, max_length=255)
    event_id: Optional[int] = None

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, v: Optional[str]) -> Optional[str]:
        """Validate URI is URL-safe."""
        if v is not None and not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "URI must be alphanumeric with hyphens or underscores only"
            )
        return v.lower() if v else v


class SessionResponse(SessionBase):
    """Schema for Session response."""

    id: int = Field(..., description="Session ID")
    owner_id: Optional[int] = Field(None, description="Session owner ID")
    available_content: List[str] = Field(
        default_factory=list,
        alias="available_content_identifiers",
        description="List of available content identifiers"
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SessionWithEvent(SessionResponse):
    """Schema for Session response with associated event."""

    event: Optional[EventResponse] = None
