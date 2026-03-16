"""Tests for WorkflowStep base class and step execution."""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.database.models import WorkflowExecutionStatus
from app.workflows.execution_context import (
    GenerationState,
    StepRegistry,
    WorkflowRegistry,
)
from app.workflows.services.execution_service import WorkflowExecutionService
from app.workflows.steps.base_step import WorkflowStep

from .test_workflows_utils import (
    MockStep,
    clean_registries,
    create_generation_state,
    create_mock_step,
    mock_db_session,
    mock_session_model,
)


@pytest.mark.asyncio
async def test_step_execute_generates_and_persists(mock_db_session):
    """Test that execute() generates content AND persists it."""
    step = create_mock_step(
        identifier="test_step",
        dependencies=[],
        generate_result={
            "content": "Generated content",
            "content_type": "plain_text",
            "meta_info": {"model": "test-model"},
        },
    )

    # Mock the persistence method
    step._save_to_db = Mock()

    # Create state
    state = create_generation_state(
        session_id=1,
        execution_id=1,
        db_session=mock_db_session,
        transcription="Test transcription",
    )

    # Execute
    result = await step.execute(
        session_id=1,
        execution_id=1,
        context={"transcription": "Test transcription"},
    )

    # Verify generate was called
    assert result == {"test_step": "Generated content"}

    # Verify persistence was called with correct parameters
    step._save_to_db.assert_called_once()
    call_args = step._save_to_db.call_args
    assert call_args[1]["identifier"] == "test_step"
    assert call_args[1]["execution_id"] == 1


@pytest.mark.asyncio
async def test_step_execute_handles_errors(mock_db_session):
    """Test that execute() handles errors gracefully."""
    step = create_mock_step(identifier="error_step")

    # Make _generate raise an error
    step._generate = AsyncMock(side_effect=ValueError("Test error"))

    state = create_generation_state(db_session=mock_db_session)

    # Execute should raise
    with pytest.raises(ValueError, match="Test error"):
        await step.execute(
            session_id=1,
            execution_id=1,
            context={},
        )


@pytest.mark.asyncio
async def test_step_context_chaining(mock_db_session):
    """Test that steps can access context from previous steps."""
    step = create_mock_step(
        identifier="dependent_step",
        dependencies=["previous_step"],
    )

    step._save_to_db = Mock()

    # Create a context with output from previous step
    context = {
        "transcription": "Test transcription",
        "previous_step": "Output from previous step",
    }

    # Execute
    result = await step.execute(
        session_id=1,
        execution_id=1,
        context=context,
    )

    # Verify the step received the context
    assert result is not None


def test_step_identifier_property():
    """Test that step identifier property works."""
    step = create_mock_step(identifier="my_step")
    assert step.identifier == "my_step"


def test_step_dependencies_property():
    """Test that step dependencies property works."""
    step = create_mock_step(
        identifier="my_step",
        dependencies=["step1", "step2"],
    )
    assert step.dependencies == ["step1", "step2"]


def test_step_get_model_config():
    """Test that get_model_config returns default config."""
    step = create_mock_step(identifier="test_step")
    config = step.get_model_config()

    assert config.model == "gemma-3-27b-it"
    assert config.temperature == 0.7
    assert config.max_tokens == 2000


@pytest.mark.asyncio
async def test_summary_step_integration(mock_db_session, mock_session_model):
    """Test SummaryStep with mocked LLM."""
    from app.workflows.steps.summary_step import SummaryStep

    step = SummaryStep()

    # Mock the LLM
    mock_response = Mock()
    mock_response.content = "# Test Summary\n\nSummary content"

    with (
        patch.object(step, "get_model") as mock_get_model,
        patch("app.database.connection.SessionLocal") as mock_session_local,
    ):
        # Create mock LLM with ainvoke method
        mock_llm = Mock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_get_model.return_value = mock_llm

        # Mock database session creation
        mock_session_local.return_value = mock_db_session

        # Mock database query
        mock_db_session.query = Mock(
            return_value=Mock(
                filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
            )
        )

        # Mock persistence
        step._save_to_db = Mock()

        # Execute
        result = await step.execute(
            session_id=1,
            execution_id=1,
            context={"transcription": "Test transcription"},
        )

        # Verify result
        assert "summary" in result
        assert result["summary"] == "# Test Summary\n\nSummary content"

        # Verify persistence was called
        step._save_to_db.assert_called_once()


@pytest.mark.asyncio
async def test_step_with_callable_generate():
    """Test step with callable generate result."""

    def generate_func(session_id, context):
        return {
            "content": f"Content for session {session_id}",
            "content_type": "plain_text",
            "meta_info": {},
        }

    step = create_mock_step(
        identifier="callable_step",
        generate_result=generate_func,
    )

    step._save_to_db = Mock()

    result = await step.execute(
        session_id=5,
        execution_id=1,
        context={},
    )

    assert result["callable_step"] == "Content for session 5"


@pytest.mark.asyncio
async def test_basic_workflow_creation(mock_db_session, mock_session_model, clean_registries):
    """Test basic workflow record creation."""
    step = create_mock_step(identifier="summary", dependencies=[])
    StepRegistry.register(step)

    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_session_model)))
        )
    )

    # Mock Celery to prevent actual task queueing
    with patch("app.async_jobs.tasks.execute_generated_content.apply_async"):
        workflow_exec, task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",
            db=mock_db_session,
        )

    # Verify execution record created
    assert workflow_exec is not None
    assert workflow_exec.session_id == 1
    assert workflow_exec.target == "summary"
    assert workflow_exec.status == WorkflowExecutionStatus.QUEUED
    assert task_id is not None


@pytest.mark.asyncio
async def test_execution_status_lifecycle(mock_db_session, clean_registries):
    """Test execution status transitions."""
    from datetime import datetime

    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.QUEUED,
        started_at=None,
        completed_at=None,
        error=None,
        created_at=datetime.utcnow(),
    )

    mock_db_session.query = Mock(
        return_value=Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution))))
    )
    mock_db_session.commit = Mock()

    # Test QUEUED -> RUNNING
    WorkflowExecutionService.mark_running(
        execution_id=1, celery_task_id="task_123", db=mock_db_session
    )
    assert mock_execution.status == WorkflowExecutionStatus.RUNNING
    assert mock_execution.started_at is not None

    # Test RUNNING -> COMPLETED
    WorkflowExecutionService.mark_completed(execution_id=1, db=mock_db_session)
    assert mock_execution.status == WorkflowExecutionStatus.COMPLETED
    assert mock_execution.completed_at is not None


@pytest.mark.asyncio
async def test_execution_failure_marking(mock_db_session, clean_registries):
    """Test marking execution as failed."""
    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.RUNNING,
        error=None,
    )

    mock_db_session.query = Mock(
        return_value=Mock(filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution))))
    )
    mock_db_session.commit = Mock()

    error_msg = "Step failed: LLM timeout"
    WorkflowExecutionService.mark_failed(
        execution_id=1,
        db=mock_db_session,
        error=error_msg,
    )

    assert mock_execution.status == WorkflowExecutionStatus.FAILED
    assert mock_execution.error == error_msg


@pytest.mark.asyncio
async def test_workflow_with_dependencies(mock_db_session, mock_session_model, clean_registries):
    """Test workflow with step dependencies."""
    # Create workflow with dependencies: step1 -> step2
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])

    StepRegistry.register(step1)
    StepRegistry.register(step2)

    # Register workflow class for this test
    from .test_workflows_utils import create_test_workflow

    workflow_class = create_test_workflow("dependent_workflow", ["step1", "step2"])
    WorkflowRegistry.register_workflow_class("dependent_workflow", workflow_class)

    # Test that workflow is properly registered
    assert "dependent_workflow" in WorkflowRegistry.get_all_workflow_classes()
    assert WorkflowRegistry.get_workflow_class("dependent_workflow") == workflow_class


@pytest.mark.asyncio
async def test_step_dependency_chain(clean_registries):
    """Test that workflow respects step dependencies."""
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])

    StepRegistry.register(step1)
    StepRegistry.register(step2)
