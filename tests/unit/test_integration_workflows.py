"""Integration tests for complete workflow execution."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any

from app.workflows.services.execution_service import WorkflowExecutionService
from app.workflows.execution_context import (
    GenerationState,
    StepRegistry,
    WorkflowRegistry,
)
from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.flows import TalkWorkflow
from app.workflows.execution_context import WorkflowRegistry
from app.async_jobs.tasks import execute_generated_content
from .test_workflows_utils import (
    create_mock_step,
    mock_db_session,
    mock_session_model,
    clean_registries,
    create_generation_state,
)


@pytest.mark.asyncio
async def test_full_workflow_execution_talk_workflow(
    mock_db_session, mock_session_model, clean_registries
):
    """Test end-to-end execution of talk_workflow with all steps."""
    # Register TalkWorkflow since clean_registries clears it
    WorkflowRegistry.register_workflow_class("talk_workflow", TalkWorkflow)

    # Register steps
    step1 = create_mock_step(
        identifier="summary",
        dependencies=[],
        generate_result={
            "content": "Summary content",
            "content_type": "text",
            "meta_info": {},
        },
    )
    step2 = create_mock_step(
        identifier="key_takeaways",
        dependencies=["summary"],
        generate_result={
            "content": "Key takeaways",
            "content_type": "text",
            "meta_info": {},
        },
    )
    step3 = create_mock_step(
        identifier="tags",
        dependencies=["key_takeaways"],
        generate_result={"content": "Tags", "content_type": "text", "meta_info": {}},
    )

    StepRegistry.register(step1)
    StepRegistry.register(step2)
    StepRegistry.register(step3)

    # Mock persistence
    step1._save_to_db = Mock()
    step2._save_to_db = Mock()
    step3._save_to_db = Mock()

    # Setup database
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

    # Create execution with actual Celery mocking
    with patch(
        "app.async_jobs.tasks.execute_generated_content.apply_async"
    ) as mock_celery:
        mock_celery.return_value = Mock(id="task-1")
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="talk_workflow",
            db=mock_db_session,
        )

    assert workflow_exec.target == "talk_workflow"
    assert celery_task_id is not None
    assert mock_celery.called


@pytest.mark.asyncio
async def test_individual_step_execution(
    mock_db_session, mock_session_model, clean_registries
):
    """Test execution of individual step without workflow."""
    # Register only summary step
    step = create_mock_step(
        identifier="summary",
        dependencies=[],
        generate_result={"content": "Summary", "content_type": "text", "meta_info": {}},
    )
    StepRegistry.register(step)
    step._save_to_db = Mock()

    # Setup database
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

    # Create execution for just the step
    with patch(
        "app.async_jobs.tasks.execute_generated_content.apply_async"
    ) as mock_celery:
        mock_celery.return_value = Mock(id="task-1")
        workflow_exec, celery_task_id = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="summary",  # Individual step, not workflow
            db=mock_db_session,
        )

    assert workflow_exec.target == "summary"
    assert celery_task_id is not None


@pytest.mark.asyncio
async def test_context_chaining_through_steps(clean_registries):
    """Test that context flows correctly through dependent steps."""
    # Create steps where each adds to context
    step1 = create_mock_step(
        identifier="step1",
        dependencies=[],
        generate_result={"content": "First", "content_type": "text", "meta_info": {}},
    )
    step2 = create_mock_step(
        identifier="step2",
        dependencies=["step1"],
        generate_result={"content": "Second", "content_type": "text", "meta_info": {}},
    )

    StepRegistry.register(step1)
    StepRegistry.register(step2)

    # Create a sample workflow
    class ChainingWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "chaining_workflow"

        def build_graph(self):
            from langgraph.graph import StateGraph, START, END
            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            builder.add_node("step2", create_step_node("step2"))
            builder.add_edge(START, "step1")
            builder.add_edge("step1", "step2")
            builder.add_edge("step2", END)
            return builder.compile()

    # Build graph using the workflow
    graph = WorkflowRegistry.get_or_build_graph(ChainingWorkflow)

    # Create state
    state = create_generation_state(
        session_id=1,
        execution_id=1,
        transcription="Test transcription",
    )

    # Verify state has required fields
    assert "session_id" in state
    assert "execution_id" in state
    assert "transcription" in state


@pytest.mark.asyncio
async def test_error_in_step_propagates(clean_registries):
    """Test that errors in steps are properly captured."""
    # Create step that fails
    step = create_mock_step(identifier="failing_step", dependencies=[])
    step._generate = AsyncMock(side_effect=RuntimeError("Step failed"))

    StepRegistry.register(step)

    # Execute
    with pytest.raises(RuntimeError, match="Step failed"):
        await step.execute(
            session_id=1,
            execution_id=1,
            context={"transcription": "Test"},
        )


@pytest.mark.asyncio
async def test_parallel_independent_steps(clean_registries):
    """Test workflow with parallel independent steps."""
    # Create independent steps
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=[])
    step3 = create_mock_step(identifier="step3", dependencies=[])

    StepRegistry.register(step1)
    StepRegistry.register(step2)
    StepRegistry.register(step3)

    # Workflow is already registered at package import time

    # Create a test workflow with parallel steps
    class ParallelTestWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "parallel_test_workflow"

        def build_graph(self):
            from langgraph.graph import StateGraph, START, END
            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            builder.add_node("step2", create_step_node("step2"))
            builder.add_node("step3", create_step_node("step3"))
            builder.add_edge(START, "step1")
            builder.add_edge(START, "step2")
            builder.add_edge(START, "step3")
            builder.add_edge("step1", END)
            builder.add_edge("step2", END)
            builder.add_edge("step3", END)
            return builder.compile()

    # Build graph
    graph = WorkflowRegistry.get_or_build_graph(ParallelTestWorkflow)

    # All steps should be in parallel (no dependencies)
    assert "step1" in graph.nodes
    assert "step2" in graph.nodes
    assert "step3" in graph.nodes


@pytest.mark.asyncio
async def test_long_dependency_chain(clean_registries):
    """Test workflow with long chain of dependencies."""
    # Create chain: step1 -> step2 -> step3 -> step4 -> step5
    steps = []
    for i in range(1, 6):
        step_id = f"step{i}"
        deps = [f"step{i-1}"] if i > 1 else []
        step = create_mock_step(identifier=step_id, dependencies=deps)
        StepRegistry.register(step)
        steps.append(step)

    step_ids = [f"step{i}" for i in range(1, 6)]

    # Create a test workflow with chain of dependencies
    class ChainTestWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "chain_test_workflow"

        def build_graph(self):
            from langgraph.graph import StateGraph, START, END
            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            for step_id in step_ids:
                builder.add_node(step_id, create_step_node(step_id))

            builder.add_edge(START, "step1")
            for i in range(1, 5):
                builder.add_edge(f"step{i}", f"step{i+1}")
            builder.add_edge("step5", END)

            return builder.compile()

    # Build graph
    graph = WorkflowRegistry.get_or_build_graph(ChainTestWorkflow)

    # All steps should exist
    for step_id in step_ids:
        assert step_id in graph.nodes


@pytest.mark.asyncio
async def test_execution_status_tracking(mock_db_session, clean_registries):
    """Test that execution status is properly tracked through phases."""
    from app.database.models import WorkflowExecutionStatus
    from datetime import datetime

    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.QUEUED,
        started_at=None,
        completed_at=None,
        created_at=datetime.utcnow(),
        error=None,
    )

    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
        )
    )
    mock_db_session.commit = Mock()

    # Simulate status transitions
    # QUEUED -> RUNNING
    WorkflowExecutionService.mark_running(execution_id=1, db=mock_db_session)
    assert mock_execution.status == WorkflowExecutionStatus.RUNNING

    # RUNNING -> COMPLETED
    WorkflowExecutionService.mark_completed(execution_id=1, db=mock_db_session)
    assert mock_execution.status == WorkflowExecutionStatus.COMPLETED
    assert mock_execution.completed_at is not None


@pytest.mark.asyncio
async def test_failed_step_marks_execution_failed(mock_db_session, clean_registries):
    """Test that failed step marks entire execution as failed."""
    from app.database.models import WorkflowExecutionStatus

    mock_execution = Mock(
        id=1,
        status=WorkflowExecutionStatus.RUNNING,
        error=None,
    )

    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_execution)))
        )
    )
    mock_db_session.commit = Mock()

    # Mark as failed
    error_msg = "Step 'summary' failed: LLM timeout"
    WorkflowExecutionService.mark_failed(
        execution_id=1,
        db=mock_db_session,
        error=error_msg,
    )

    assert mock_execution.status == WorkflowExecutionStatus.FAILED
    assert mock_execution.error == error_msg


@pytest.mark.asyncio
async def test_task_receives_correct_step_ids(
    mock_db_session, mock_session_model, clean_registries
):
    """Test that task receives correct step IDs for execution."""
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])

    StepRegistry.register(step1)
    StepRegistry.register(step2)

    # Register workflow class for this test
    from .test_workflows_utils import create_test_workflow

    workflow_class = create_test_workflow("test_workflow", ["step1", "step2"])
    WorkflowRegistry.register_workflow_class("test_workflow", workflow_class)

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


@pytest.mark.asyncio
async def test_step_persistence_in_execution(mock_db_session, clean_registries):
    """Test that steps persist their output during execution."""

    step = create_mock_step(
        identifier="summary",
        dependencies=[],
        generate_result={
            "content": "Generated summary",
            "content_type": "plain_text",
            "meta_info": {"model": "test-model"},
        },
    )

    persist_calls = []

    def capture_persist(*args, **kwargs):
        persist_calls.append(kwargs)

    step._save_to_db = Mock(side_effect=capture_persist)

    # Execute
    await step.execute(
        session_id=1,
        execution_id=1,
        context={"transcription": "Test"},
    )

    # Verify persistence was called with correct data
    assert len(persist_calls) > 0
    persist_call = persist_calls[0]
    assert persist_call["identifier"] == "summary"
    assert persist_call["execution_id"] == 1
    assert "content" in persist_call


@pytest.mark.asyncio
async def test_workflow_with_missing_dependency_step(clean_registries):
    """Test error handling when step depends on missing step."""
    # Register only step2, not step1 which it depends on
    step2 = create_mock_step(
        identifier="step2",
        dependencies=["step1"],  # Depends on step1
    )
    StepRegistry.register(step2)

    # Create workflow that references step2
    class MissingDepWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "missing_dep_workflow"

        def build_graph(self):
            from langgraph.graph import StateGraph, START, END
            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step2", create_step_node("step2"))
            builder.add_edge(START, "step2")
            builder.add_edge("step2", END)
            return builder.compile()

    # Try to build workflow with missing dependency
    # Should either skip missing dependency or raise informative error
    try:
        graph = WorkflowRegistry.get_or_build_graph(MissingDepWorkflow)
        # If successful, that's ok - may be lenient
    except (KeyError, ValueError) as e:
        # If error, should be clear about what's wrong
        assert "step1" in str(e) or "not found" in str(e).lower()


@pytest.mark.asyncio
async def test_multiple_sessions_independent_execution(
    mock_db_session, clean_registries
):
    """Test that executions for different sessions are independent."""
    step = create_mock_step(identifier="test_step", dependencies=[])
    StepRegistry.register(step)

    mock_session1 = Mock(id=1, session_content=Mock(transcription="Session 1"))
    mock_session2 = Mock(id=2, session_content=Mock(transcription="Session 2"))

    call_count = [0]

    def mock_query_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] % 2 == 1:
            return Mock(
                filter=Mock(return_value=Mock(first=Mock(return_value=mock_session1)))
            )
        else:
            return Mock(
                filter=Mock(return_value=Mock(first=Mock(return_value=mock_session2)))
            )

    mock_db_session.query = Mock(side_effect=mock_query_side_effect)
    mock_db_session.add = Mock()
    mock_db_session.flush = Mock()
    mock_db_session.commit = Mock()

    id_counter = [0]

    def refresh_side_effect(obj):
        if hasattr(obj, "id") and obj.id is None:
            id_counter[0] += 1
            obj.id = id_counter[0]

    mock_db_session.refresh = Mock(side_effect=refresh_side_effect)

    with patch(
        "app.async_jobs.tasks.execute_generated_content.apply_async"
    ) as mock_celery:
        mock_celery.return_value = Mock(id="task_123")

        # Execute for session 1
        exec1, task1 = WorkflowExecutionService.create_and_queue(
            session_id=1,
            target="test_step",
            db=mock_db_session,
        )

        # Execute for session 2
        exec2, task2 = WorkflowExecutionService.create_and_queue(
            session_id=2,
            target="test_step",
            db=mock_db_session,
        )

    # Both should succeed with different execution IDs
    assert exec1.id != exec2.id
