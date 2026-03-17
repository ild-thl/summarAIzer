"""Pytest configuration and fixtures."""

# CRITICAL: Mock boto3 BEFORE any app imports to prevent S3 client initialization
# during module import time (ImageStep registers itself at module load)
from datetime import datetime, timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker

from app.database.connection import get_db
from app.database.models import (
    APIKey,
    Base,
    Event,
    EventStatus,
    SessionFormat,
    SessionStatus,
    User,
)
from app.database.models import (
    Session as SessionModel,
)
from main import app

_boto3_patcher = mock.patch("boto3.client")
_mock_boto3 = _boto3_patcher.start()
# Configure the mock to return a safe mock client
_mock_s3_client = mock.MagicMock()
_mock_s3_client.put_object.return_value = {}
_mock_s3_client.get_object.return_value = {"Body": mock.MagicMock()}
_mock_boto3.return_value = _mock_s3_client


def pytest_configure(config):
    """Ensure boto3 mock stays active for entire test session."""
    # The mock started at module level continues throughout all tests
    pass


def pytest_sessionfinish(session, exitstatus):
    """Clean up boto3 mock after tests complete."""
    _boto3_patcher.stop()


@pytest.fixture(autouse=True)
def clear_overrides():
    """Clear dependency overrides before and after each test."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_db(tmp_path):
    """Create a fresh test database for each test."""
    # Use a file-based database for the test instead of in-memory
    # because SQLite in-memory databases are thread-local
    # and TestClient runs the app in a different thread
    db_path = tmp_path / "test.db"
    database_url = f"sqlite:///{db_path}"

    # Create test engine with SQLite file-based database
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )

    # Enable foreign keys for SQLite
    @sqlalchemy_event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables in test database
    # This MUST happen before any queries
    Base.metadata.create_all(bind=engine)

    # Verify tables were created
    from sqlalchemy import inspect as sa_inspect

    inspector_obj = sa_inspect(engine)
    tables = inspector_obj.get_table_names()
    if not tables:
        raise RuntimeError("Failed to create test tables. Tables: " + str(tables))

    # Create session factory
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override get_db dependency BEFORE creating client
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Return session for direct use in tests if needed
    session = TestingSessionLocal()
    yield session

    # Cleanup
    session.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_db):
    """Create a test client with test database."""
    # test_db has already set up the dependency overrides
    # Just create and return the client
    return TestClient(app)


@pytest.fixture
def sample_event(test_db, sample_user):
    """Create a sample event for testing."""
    now = datetime.utcnow()
    event = Event(
        title="Test Event",
        description="A test event",
        start_date=now,
        end_date=now + timedelta(days=2),
        location="Test Location",
        status=EventStatus.DRAFT,
        uri="test-event",
        owner_id=sample_user.id,
    )
    test_db.add(event)
    test_db.commit()
    test_db.refresh(event)
    return event


@pytest.fixture
def sample_session(test_db, sample_event, sample_user):
    """Create a sample session for testing."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Test Session",
        speakers=["John Doe", "Jane Smith"],
        tags=["AI", "Testing"],
        short_description="A test session",
        location="Room 101",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.DRAFT,
        session_format=SessionFormat.WORKSHOP,
        duration=60,
        language="en",
        uri="test-session",
        event_id=sample_event.id,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session


@pytest.fixture
def sample_session_no_event(test_db, sample_user):
    """Create a sample session without an event."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Standalone Session",
        speakers=["Jane Doe", "Bob Johnson"],
        tags=["Development"],
        short_description="A standalone test session",
        location="Room 202",
        start_datetime=now + timedelta(days=1),
        end_datetime=now + timedelta(days=1, hours=2),
        status=SessionStatus.PUBLISHED,
        session_format=SessionFormat.LIGHTNING_TALK,
        duration=120,
        language="en",
        uri="standalone-session",
        event_id=None,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session


@pytest.fixture
def sample_user(test_db):
    """Create a sample user (API service account)."""
    user = User(
        username="test-scheduler",
        type="api",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def sample_user_human(test_db):
    """Create a sample human user."""
    user = User(
        username="test-human",
        email="test@example.com",
        type="human",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _hash_api_key(key: str) -> str:
    """Hash API key for storage."""
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture
def sample_api_key(test_db, sample_user):
    """Create a sample API key for testing."""
    plain_key = "test-api-key-12345"
    api_key = APIKey(
        user_id=sample_user.id,
        key_hash=_hash_api_key(plain_key),
        name="test-key",
    )
    test_db.add(api_key)
    test_db.commit()
    test_db.refresh(api_key)
    return api_key, plain_key  # Return both hashed and plain for testing


@pytest.fixture
def event_with_owner(test_db, sample_user):
    """Create an event with an owner."""
    now = datetime.utcnow()
    event = Event(
        title="Owned Event",
        description="Event with owner",
        start_date=now,
        end_date=now + timedelta(days=2),
        location="Test Location",
        status=EventStatus.DRAFT,
        uri="owned-event",
        owner_id=sample_user.id,
    )
    test_db.add(event)
    test_db.commit()
    test_db.refresh(event)
    return event


@pytest.fixture
def session_with_owner(test_db, event_with_owner, sample_user):
    """Create a session with an owner."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Owned Session",
        speakers=["Speaker"],
        tags=["Category"],
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.DRAFT,
        uri="owned-session",
        event_id=event_with_owner.id,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session


@pytest.fixture
def published_session(test_db, sample_event, sample_user):
    """Create a published session for testing public access."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Published Test Session",
        speakers=["John Doe"],
        tags=["Testing"],
        short_description="A published test session",
        location="Room 101",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.PUBLISHED,
        session_format=SessionFormat.WORKSHOP,
        duration=60,
        language="en",
        uri="published-test-session",
        event_id=sample_event.id,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session


@pytest.fixture
def mock_db_session():
    """Create a mock database session with common methods and refresh side effect."""
    from sqlalchemy.orm import Session as SQLSession

    db = mock.Mock(spec=SQLSession)
    db.query = mock.Mock()
    db.add = mock.Mock()
    db.commit = mock.Mock()
    db.rollback = mock.Mock()

    # Configure refresh to assign an ID if the object doesn't have one
    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1  # Assign mock ID

    db.refresh = mock.Mock(side_effect=refresh_side_effect)
    return db


@pytest.fixture
def mock_session_model():
    """Create a mock Session database model with realistic test data."""
    session_mock = mock.Mock(spec=SessionModel)
    session_mock.id = 1
    session_mock.title = "Test Session"
    session_mock.speakers = ["Speaker 1", "Speaker 2"]
    session_mock.tags = ["Category 1"]
    session_mock.duration = 60
    return session_mock


@pytest.fixture
def clean_registries():
    """Clean step and workflow registries before and after each test.

    Saves and restores original state to avoid contaminating other tests.
    """
    from app.workflows.execution_context import StepRegistry, WorkflowRegistry

    # Save original state
    original_steps = StepRegistry.get_all_steps().copy()
    original_workflow_classes = WorkflowRegistry.get_all_workflow_classes().copy()

    # Clear before test
    StepRegistry.clear()
    WorkflowRegistry.clear()

    yield

    # Restore original state
    StepRegistry.clear()
    WorkflowRegistry.clear()

    # Re-register original steps
    for _, step in original_steps.items():
        StepRegistry.register(step)

    # Re-register original workflow classes
    for workflow_name, workflow_class in original_workflow_classes.items():
        WorkflowRegistry.register_workflow_class(workflow_name, workflow_class)
