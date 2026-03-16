"""Pydantic schemas for content and workflow operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class GeneratedContentCreate(BaseModel):
    """Schema for creating new generated content."""

    content: str = Field(..., description="The actual content payload")
    content_type: str = Field(
        default="plain_text",
        description="Content format type (e.g., 'plain_text', 'markdown', 'json_array')",
    )
    meta_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata (e.g., model, tokens used, source)",
    )


class GeneratedContentUpdate(BaseModel):
    """Schema for updating existing content (identifier is in URL, not body)."""

    content: str = Field(..., description="The updated content payload")
    meta_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata to update",
    )


class GeneratedContentResponse(BaseModel):
    """Schema for generated content response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    identifier: str
    content_type: str
    content: str
    workflow_execution_id: Optional[int] = None
    meta_info: Optional[Dict[str, Any]] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class GeneratedContentListItem(BaseModel):
    """Lightweight schema for listing content (without full content payload)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    identifier: str
    content_type: str
    workflow_execution_id: Optional[int] = None
    created_at: datetime
    created_by_user_id: Optional[int] = None


class WorkflowExecutionCreate(BaseModel):
    """Schema for creating workflow execution."""

    workflow_type: str = Field(..., description="Type of workflow to execute")


class WorkflowExecutionResponse(BaseModel):
    """Schema for workflow execution response."""

    task_id: str = Field(..., description="Celery task ID for polling status")
    workflow_type: str
    status: str
    created_at: datetime


class WorkflowStatusResponse(BaseModel):
    """Schema for polling workflow status."""

    status: str  # "queued", "running", "completed", "failed"
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_content: List[GeneratedContentListItem] = Field(
        default_factory=list,
        description="Content items created by workflow (populated when completed)",
    )


class SessionContentListResponse(BaseModel):
    """Session response with available content identifiers."""

    available_content: List[str] = Field(
        default_factory=list,
        description="List of available content identifiers for session",
    )


class ContentPublishRequest(BaseModel):
    """Request to publish/approve generated content (future feature)."""

    identifier: str = Field(..., description="Content identifier to publish")
    content_id: Optional[int] = Field(
        None,
        description="Specific generated_content ID to publish (if multiple versions exist)",
    )
