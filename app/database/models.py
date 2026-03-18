"""SQLAlchemy ORM models."""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class SessionStatus(str, Enum):
    """Session status enumeration."""

    DRAFT = "draft"
    PUBLISHED = "published"


class SessionFormat(str, Enum):
    """Session format enumeration."""

    INPUT = "input"
    LIGHTNING_TALK = "lightning talk"
    DISCUSSION = "diskussion"
    WORKSHOP = "workshop"
    TRAINING = "training"


class EventStatus(str, Enum):
    """Event status enumeration."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class WorkflowExecutionStatus(str, Enum):
    """Workflow execution status enumeration."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Event(Base):
    """Event model representing a conference or festival event."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    start_date = Column(DateTime, nullable=False, index=True)
    end_date = Column(DateTime, nullable=False, index=True)
    location = Column(String(255), nullable=True)
    status = Column(SQLEnum(EventStatus), default=EventStatus.DRAFT, index=True)
    uri = Column(String(255), nullable=False, unique=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    sessions = relationship("Session", back_populates="event", cascade="all, delete-orphan")
    owner = relationship("User", back_populates="events", foreign_keys=[owner_id])


class Session(Base):
    """Session model representing a talk, workshop, or other presentation."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    speakers = Column(JSON, default=list, nullable=True)  # Array of speaker objects
    tags = Column(JSON, default=list, nullable=True)  # Array of tag strings
    short_description = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    start_datetime = Column(DateTime, nullable=False, index=True)
    end_datetime = Column(DateTime, nullable=False)
    recording_url = Column(String(500), nullable=True)
    status = Column(SQLEnum(SessionStatus), default=SessionStatus.DRAFT, index=True)
    session_format = Column(SQLEnum(SessionFormat), nullable=True)
    duration = Column(Integer, nullable=True)  # Duration in minutes
    language = Column(String(10), default="en", nullable=False)  # ISO 639-1 code
    uri = Column(String(255), nullable=False, index=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    available_content_identifiers = Column(
        JSON, default=list, nullable=False
    )  # ["transcription", "summary", "tags"]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("event_id", "uri", name="uq_session_uri_per_event"),)

    # Relationships
    event = relationship("Event", back_populates="sessions")
    owner = relationship("User", back_populates="sessions", foreign_keys=[owner_id])
    content_items = relationship(
        "GeneratedContent", back_populates="session", cascade="all, delete-orphan"
    )


class User(Base):
    """Generic user model supporting both API service accounts and human users."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=True)  # For human users
    type = Column(String(20), default="api", nullable=False)  # 'api' or 'human'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="owner")
    sessions = relationship("Session", back_populates="owner")


class APIKey(Base):
    """API keys for authentication (supports multiple keys per user and key rotation)."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash = Column(String(255), unique=True, nullable=False, index=True)  # Hashed
    name = Column(String(255), nullable=True)  # e.g., 'scheduler-service', 'mobile-app-v2'
    last_used_at = Column(DateTime, nullable=True)  # For audit trail
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    deleted_at = Column(DateTime, nullable=True)  # Soft delete for audit trail

    # Relationships
    user = relationship("User", back_populates="api_keys")


class WorkflowExecution(Base):
    """Tracks execution of generative workflows."""

    __tablename__ = "workflow_executions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target = Column(String(255), nullable=False, index=True)  # "talk_workflow", "summary", etc.
    status = Column(
        SQLEnum(WorkflowExecutionStatus),
        default=WorkflowExecutionStatus.QUEUED,
        nullable=False,
        index=True,
    )
    celery_task_id = Column(String(255), nullable=True, index=True)  # For tracking Celery tasks
    error = Column(Text, nullable=True)
    triggered_by = Column(
        String(20), nullable=False, default="manual"
    )  # "user_triggered", "auto_scheduled"
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    session = relationship("Session")
    created_by = relationship("User")
    generated_contents = relationship("GeneratedContent", back_populates="workflow_execution")


class GeneratedContent(Base):
    """Stores all generated content (summaries, tags, transcriptions, etc.)."""

    __tablename__ = "generated_content"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identifier = Column(
        String(255), nullable=False, index=True
    )  # "transcription", "summary", "tags", "key_takeaways", etc.
    content_type = Column(
        String(50), nullable=False
    )  # "plain_text", "markdown", "json_array", "json_object", "image_url", etc.
    content = Column(Text, nullable=False)  # The actual content payload
    workflow_execution_id = Column(
        Integer,
        ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        nullable=True,
    )  # NULL = manually provided content (e.g., transcription on creation)
    meta_info = Column(JSON, nullable=True)  # Optional: {model, tokens, source, etc.}
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "identifier",
            "workflow_execution_id",
            name="uq_content_per_session_workflow",
        ),
    )

    # Relationships
    session = relationship("Session", back_populates="content_items")
    workflow_execution = relationship("WorkflowExecution", back_populates="generated_contents")
    created_by = relationship("User")
