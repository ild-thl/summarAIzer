"""Pydantic schemas for API key self-management."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class APIKeyCreateRequest(BaseModel):
    """Request payload for creating an API key."""

    name: str | None = Field(default=None, description="Optional display name for the API key")
    allowed_roles: list[str] | None = Field(
        default=None,
        description="Optional delegated role subset. Must be subset of current user's roles.",
    )


class APIKeyCreateResponse(BaseModel):
    """Response payload for a newly created API key (secret shown once)."""

    id: int
    name: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    key: str = Field(description="Plain API key. Only returned once on creation.")
    created_at: datetime


class APIKeyListItem(BaseModel):
    """List item for API keys without exposing secret material."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    last_used_at: datetime | None = None
    created_at: datetime
    deleted_at: datetime | None = None


class APIKeyListResponse(BaseModel):
    """Response payload for listing current user's API keys."""

    keys: list[APIKeyListItem] = Field(default_factory=list)
