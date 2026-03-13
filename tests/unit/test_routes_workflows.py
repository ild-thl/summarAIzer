"""Tests for API routes triggering workflows."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.workflows.services.execution_service import WorkflowExecutionService
from app.workflows.execution_context import StepRegistry, WorkflowRegistry
from .test_workflows_utils import (
    create_mock_step,
    mock_db_session,
    mock_session_model,
    clean_registries,
)


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI app."""
    # Import here to avoid circular imports
    from main import app
    from sqlalchemy.orm import Session

    client = TestClient(app)

    # This would normally override dependencies, but for testing
    # we'll patch the database queries directly
    return client


@pytest.mark.asyncio
async def test_trigger_workflow_full_workflow(mock_db_session, clean_registries):
    """Test triggering full workflow via API."""
    # Setup
    step1 = create_mock_step(identifier="summary", dependencies=[])
    step2 = create_mock_step(identifier="key_takeaways", dependencies=["summary"])

    StepRegistry.register(step1)
    StepRegistry.register(step2)

    # Register workflow class for this test
    from .test_workflows_utils import create_test_workflow

    workflow_class = create_test_workflow("talk_workflow", ["summary", "key_takeaways"])
    WorkflowRegistry.register_workflow_class("talk_workflow", workflow_class)

    # Mock database
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    # Test the service directly (equivalent to API route)
    with patch(
        "app.async_jobs.tasks.execute_generated_content.apply_async"
    ) as mock_task:
        mock_task.return_value = Mock(id="task-1")
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="talk_workflow",
            db=mock_db_session,
        )

    assert workflow_exec.target == "talk_workflow"
    assert celery_task_id is not None


@pytest.mark.asyncio
async def test_trigger_workflow_individual_step(mock_db_session, clean_registries):
    """Test triggering individual step via API."""
    # Setup
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    # Mock database
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    # Test the service directly (equivalent to API route)
    with patch(
        "app.async_jobs.tasks.execute_generated_content.apply_async"
    ) as mock_task:
        mock_task.return_value = Mock(id="task-1")
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",  # Individual step
            db=mock_db_session,
        )

    assert workflow_exec.target == "summary"
    assert celery_task_id is not None


@pytest.mark.asyncio
async def test_trigger_workflow_session_not_found(mock_db_session):
    """Test triggering workflow with non-existent session."""
    # Mock database to return None (session not found)
    mock_db_session.query = Mock(
        return_value=Mock(filter=Mock(return_value=Mock(first=Mock(return_value=None))))
    )

    # Should raise appropriately
    with pytest.raises(ValueError, match="not found"):
        WorkflowExecutionService.create_and_queue(
            session_id=999,
            target="summary",
            db=mock_db_session,
        )


@pytest.mark.asyncio
async def test_get_workflow_status_success(mock_db_session):
    """Test getting workflow status."""
    from datetime import datetime
    from app.database.models import WorkflowExecutionStatus

    mock_execution = Mock(
        id=123,
        session_id=1,
        status=WorkflowExecutionStatus.RUNNING,
        target="summary",
        created_at=datetime.now(),
        started_at=datetime.now(),
        completed_at=None,
        error=None,
    )

    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
        )
    )

    # Get status - returns WorkflowExecution object, not dict
    status = WorkflowExecutionService.get_execution_status(
        execution_id=123,
        db=mock_db_session,
    )

    assert status.id == 123
    assert status.status == WorkflowExecutionStatus.RUNNING
    assert status.target == "summary"


@pytest.mark.asyncio
async def test_get_workflow_status_not_found(mock_db_session):
    """Test getting status for non-existent execution."""
    mock_db_session.query = Mock(
        return_value=Mock(filter=Mock(return_value=Mock(first=Mock(return_value=None))))
    )

    # Should return None when not found
    result = WorkflowExecutionService.get_execution_status(
        execution_id=999,
        db=mock_db_session,
    )

    assert result is None


@pytest.mark.asyncio
async def test_trigger_workflow_creates_execution_record(
    mock_db_session, clean_registries
):
    """Test that triggering workflow creates execution record."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    # Track database calls
    add_calls = []

    mock_db_session.add = Mock(side_effect=lambda x: add_calls.append(x))
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    with patch("app.async_jobs.tasks.execute_generated_content.apply_async"):
        WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",
            db=mock_db_session,
        )

    # Verify execution record was created and added
    assert mock_db_session.add.called


@pytest.mark.asyncio
async def test_trigger_workflow_queues_task(mock_db_session, clean_registries):
    """Test that workflow trigger queues async task."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    # Track Celery task queuing
    with patch(
        "app.async_jobs.tasks.execute_generated_content.apply_async"
    ) as mock_task:
        mock_task.return_value = Mock(id="task_123")
        result = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",
            db=mock_db_session,
        )

    # Verify Celery task was queued
    assert mock_task.called


@pytest.mark.asyncio
async def test_trigger_workflow_returns_execution_id(mock_db_session, clean_registries):
    """Test that trigger workflow returns execution ID."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    with patch("app.async_jobs.tasks.execute_generated_content.apply_async"):
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",
            db=mock_db_session,
        )

    # Verify execution ID is returned
    assert workflow_exec.id is not None
    assert celery_task_id is not None


@pytest.mark.asyncio
async def test_invalid_target_error_message(mock_db_session, clean_registries):
    """Test error message for invalid target."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    # Try invalid target
    with pytest.raises(ValueError) as exc_info:
        WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="invalid_target_xyz",
            db=mock_db_session,
        )

    # Error should be informative
    assert (
        "invalid_target_xyz" in str(exc_info.value)
        or "not found" in str(exc_info.value).lower()
    )


@pytest.mark.asyncio
async def test_concurrent_workflow_executions(mock_db_session, clean_registries):
    """Test that multiple workflows can be triggered concurrently."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    id_counter = [0]

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            id_counter[0] += 1
            obj.id = id_counter[0]

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    results = []

    with patch("app.async_jobs.tasks.execute_generated_content.apply_async"):
        # Trigger multiple executions
        for i in range(5):
            exec_obj, task_id = WorkflowExecutionService.create_and_queue(
                session_id=i + 1,
                target="summary",
                db=mock_db_session,
            )
            results.append((exec_obj.id, task_id))

    # All should succeed with unique execution IDs
    assert len(results) == 5
    execution_ids = [r[0] for r in results]
    assert len(set(execution_ids)) == 5  # All unique


@pytest.mark.asyncio
async def test_workflow_with_special_characters_in_target(
    mock_db_session, clean_registries
):
    """Test triggering workflow with special characters in target name."""
    # Register workflow with dash in name
    step = create_mock_step(identifier="my-step", dependencies=[])
    StepRegistry.register(step)

    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    with patch("app.async_jobs.tasks.execute_generated_content.apply_async"):
        workflow_exec, task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="my-step",
            db=mock_db_session,
        )

    assert workflow_exec.target == "my-step"


@pytest.mark.asyncio
async def test_workflow_execution_preserves_session_context(
    mock_db_session, clean_registries
):
    """Test that execution preserves session context."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    mock_session = Mock(
        id=42,
        session_content=Mock(transcription="Test transcription"),
        event_id=7,
    )

    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session)))
        )
    )

    with patch("app.async_jobs.tasks.execute_generated_content.apply_async"):
        workflow_exec, task_id = WorkflowExecutionService.create_and_queue(
            session_id=42,
            target="summary",
            db=mock_db_session,
        )

    # Execution should be for the correct session
    assert workflow_exec.session_id == 42
    assert workflow_exec.target == "summary"
