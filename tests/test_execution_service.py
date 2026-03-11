"""Tests for WorkflowExecutionService layer."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import uuid

from app.workflows.services.execution_service import WorkflowExecutionService
from app.workflows.execution_context import (
    StepRegistry,
    WorkflowRegistry,
    is_workflow_target,
)
from app.database.models import WorkflowExecution, WorkflowExecutionStatus
from tests.test_workflows_utils import (
    create_mock_step,
    mock_db_session,
    mock_session_model,
    clean_registries,
    create_generation_state,
)


@pytest.mark.asyncio
async def test_create_and_queue_with_full_workflow(
    mock_db_session, mock_session_model, clean_registries
):
    """Test create_and_queue for full workflow execution."""
    # Setup
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])
    
    StepRegistry.register(step1)
    StepRegistry.register(step2)
    
    # Register workflow class for this test
    from tests.test_workflows_utils import create_test_workflow
    workflow_class = create_test_workflow("test_workflow", ["step1", "step2"])
    WorkflowRegistry.register_workflow_class("test_workflow", workflow_class)
    
    # Mock database
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
    ))
    
    # Mock Celery task
    with patch('app.async_jobs.tasks.execute_generated_content.apply_async') as mock_task:
        mock_task.return_value = Mock(state="PENDING")
        
        # Execute
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="test_workflow",
            db=mock_db_session,
        )
    
    # Verify result tuple
    assert workflow_exec is not None
    assert workflow_exec.id is not None
    assert celery_task_id is not None
    assert workflow_exec.target == "test_workflow"


@pytest.mark.asyncio
async def test_create_and_queue_with_individual_step(
    mock_db_session, mock_session_model, clean_registries
):
    """Test create_and_queue for individual step execution."""
    # Setup
    step1 = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step1)
    
    # Register workflow class for this test
    from tests.test_workflows_utils import create_test_workflow
    workflow_class = create_test_workflow("talk_workflow", ["summary"])
    WorkflowRegistry.register_workflow_class("talk_workflow", workflow_class)
    
    # Mock database
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
    ))
    
    # Mock Celery task
    with patch('app.async_jobs.tasks.execute_generated_content.apply_async') as mock_task:
        mock_task.return_value = Mock(state="PENDING")
        
        # Execute individual step
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",  # Individual step, not workflow
            db=mock_db_session,
        )
    
    # Verify
    assert workflow_exec.target == "summary"
    assert celery_task_id is not None


@pytest.mark.asyncio
async def test_create_and_queue_missing_session(mock_db_session, clean_registries):
    """Test create_and_queue fails when session doesn't exist."""
    step = create_mock_step(identifier="test_step", dependencies=[])
    StepRegistry.register(step)
    
    # Mock database to return None (session not found)
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=None)))
    ))
    
    # Should raise ValueError
    with pytest.raises(ValueError, match="Session .* not found"):
        WorkflowExecutionService.create_and_queue(
            session_id=999,
            target="test_step",
            db=mock_db_session,
        )


@pytest.mark.asyncio
async def test_create_and_queue_invalid_target(
    mock_db_session, mock_session_model, clean_registries
):
    """Test create_and_queue with invalid target."""
    step = create_mock_step(identifier="test_step", dependencies=[])
    StepRegistry.register(step)
    
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
    ))
    
    # Try invalid target - should raise ValueError
    with pytest.raises(ValueError, match="Unknown target"):
        WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="invalid_target",
            db=mock_db_session,
        )


def test_mark_running(mock_db_session):
    """Test marking execution as running."""
    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.QUEUED,
        started_at=None,
    )
    
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
    ))
    mock_db_session.commit = Mock()
    
    # Execute
    WorkflowExecutionService.mark_running(
        execution_id=1,
        db=mock_db_session,
    )
    
    # Verify
    assert mock_execution.status == WorkflowExecutionStatus.RUNNING


def test_mark_completed(mock_db_session):
    """Test marking execution as completed."""
    from datetime import datetime
    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.RUNNING,
        completed_at=None,
        created_at=datetime.utcnow(),
    )
    
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
    ))
    mock_db_session.commit = Mock()
    
    # Execute
    WorkflowExecutionService.mark_completed(
        execution_id=1,
        db=mock_db_session,
    )
    
    # Verify
    assert mock_execution.status == WorkflowExecutionStatus.COMPLETED
    assert mock_execution.completed_at is not None


def test_mark_failed(mock_db_session):
    """Test marking execution as failed."""
    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.RUNNING,
        error=None,
    )
    
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
    ))
    mock_db_session.commit = Mock()
    
    # Execute
    error_msg = "Test error occurred"
    WorkflowExecutionService.mark_failed(
        execution_id=1,
        db=mock_db_session,
        error=error_msg,
    )
    
    # Verify
    assert mock_execution.status == WorkflowExecutionStatus.FAILED
    assert mock_execution.error == error_msg


def test_get_execution_status(mock_db_session):
    """Test getting execution status."""
    from datetime import datetime
    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.RUNNING,
        target="test_workflow",
        created_at=datetime.now(),
        started_at=datetime.now(),
        completed_at=None,
        error=None,
    )
    
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
    ))
    
    # Execute
    status = WorkflowExecutionService.get_execution_status(
        execution_id=1,
        db=mock_db_session,
    )
    
    # Verify - returns WorkflowExecution object
    assert status.id == 1
    assert status.status == WorkflowExecutionStatus.RUNNING
    assert status.target == "test_workflow"


@pytest.mark.asyncio
async def test_is_workflow_target_workflow(clean_registries):
    """Test is_workflow_target for workflow target."""
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])
    
    StepRegistry.register(step1)
    StepRegistry.register(step2)
    
    # Register workflow class for this test
    from tests.test_workflows_utils import create_test_workflow
    workflow_class = create_test_workflow("my_workflow", ["step1", "step2"])
    WorkflowRegistry.register_workflow_class("my_workflow", workflow_class)
    
    # Check
    assert is_workflow_target("my_workflow") is True


@pytest.mark.asyncio
async def test_is_workflow_target_step(clean_registries):
    """Test is_workflow_target for step target."""
    step = create_mock_step(identifier="my_step", dependencies=[])
    StepRegistry.register(step)
    
    # Check
    assert is_workflow_target("my_step") is False


@pytest.mark.asyncio
async def test_is_workflow_target_invalid(clean_registries):
    """Test is_workflow_target for invalid target."""
    step = create_mock_step(identifier="my_step", dependencies=[])
    StepRegistry.register(step)
    
    # Try invalid target
    with pytest.raises(ValueError):
        is_workflow_target("invalid_target")


@pytest.mark.asyncio
async def test_validate_prerequisites_success(
    mock_db_session, mock_session_model, clean_registries
):
    """Test validate_and_prepare succeeds with valid prerequisites."""
    step = create_mock_step(identifier="test_step", dependencies=[])
    StepRegistry.register(step)
    
    # Setup mock model
    mock_session_model.session_content = Mock()
    mock_session_model.session_content.transcription = "Test transcription"
    
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
    ))
    
    # Validate
    is_workflow, execution_type = WorkflowExecutionService.validate_and_prepare(
        session_id=1,
        target="test_step",
        db=mock_db_session,
    )
    
    # Verify success - returns tuple (is_workflow, execution_type)
    assert is_workflow is False
    assert execution_type == "step"


@pytest.mark.asyncio
async def test_validate_prerequisites_missing_session(mock_db_session):
    """Test validate_and_prepare fails with missing session."""
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=None)))
    ))
    
    # Should raise
    with pytest.raises(ValueError, match="Session .* not found"):
        WorkflowExecutionService.validate_and_prepare(
            session_id=999,
            target="any_target",
            db=mock_db_session,
        )


@pytest.mark.asyncio
async def test_create_and_queue_returns_correct_structure(
    mock_db_session, mock_session_model, clean_registries
):
    """Test that create_and_queue returns correct tuple structure."""
    step = create_mock_step(identifier="test_step", dependencies=[])
    StepRegistry.register(step)
    
    # Register workflow class for this test
    from tests.test_workflows_utils import create_test_workflow
    workflow_class = create_test_workflow("test_workflow", ["test_step"])
    WorkflowRegistry.register_workflow_class("test_workflow", workflow_class)
    
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
    ))
    
    # Make refresh assign an ID
    def refresh_side_effect(obj):
        if hasattr(obj, 'id') and obj.id is None:
            obj.id = 1
    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    
    with patch('app.async_jobs.tasks.execute_generated_content.apply_async') as mock_task:
        mock_task.return_value = Mock(state="PENDING")
        
        result = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="test_workflow",
            db=mock_db_session,
        )
    
    # Verify response structure - should return tuple (WorkflowExecution, celery_task_id)
    assert isinstance(result, tuple)
    assert len(result) == 2
    workflow_exec, celery_task_id = result
    assert workflow_exec.id == 1
    assert workflow_exec.target == "test_workflow"
    assert celery_task_id is not None


@pytest.mark.asyncio 
async def test_multiple_executions_independent(
    mock_db_session, mock_session_model, clean_registries
):
    """Test that multiple executions are independent."""
    step = create_mock_step(identifier="test_step", dependencies=[])
    StepRegistry.register(step)
    
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()
    mock_db_session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
    ))
    
    # Counter to assign different IDs
    call_count = [0]
    def refresh_side_effect(obj):
        if hasattr(obj, 'id') and obj.id is None:
            call_count[0] += 1
            obj.id = call_count[0]
    
    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)
    
    with patch('app.async_jobs.tasks.execute_generated_content.apply_async') as mock_task:
        mock_task.return_value = Mock(state="PENDING")
        
        # Create two executions
        exec1, task_id1 = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="test_step",
            db=mock_db_session,
        )
        
        exec2, task_id2 = WorkflowExecutionService.create_and_queue(
            session_id=2,
            target="test_step",
            db=mock_db_session,
        )
    
    # Both should have different execution IDs
    assert exec1.id != exec2.id
    assert task_id1 != task_id2
