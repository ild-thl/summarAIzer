"""Pytest configuration and fixtures."""

import pytest
from unittest import mock
from datetime import datetime, timedelta
from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database.models import (
    Base,
    Event,
    Session as SessionModel,
    SessionStatus,
    EventStatus,
    SessionFormat,
    User,
    APIKey,
)
from app.database.connection import get_db
from main import app


@pytest.fixture(scope="session", autouse=True)
def mock_boto3_for_tests():
    """
    Mock boto3.client globally for all tests.

    This prevents actual S3 connections during test runs.
    Tests don't need real S3 access - they only care about service logic.
    """
    with mock.patch("boto3.client") as mock_client:
        # Create a mock S3 client that doesn't try to connect
        mock_s3_client = mock.MagicMock()
        mock_s3_client.put_object.return_value = {}
        mock_s3_client.get_object.return_value = {"Body": mock.MagicMock()}
        mock_client.return_value = mock_s3_client
        yield mock_client


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
        categories=["AI", "Testing"],
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
        categories=["Development"],
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
        categories=["Category"],
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
        categories=["Testing"],
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
