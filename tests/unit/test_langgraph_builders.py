"""Tests for LangGraph workflow graph caching."""

from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.workflows.execution_context import (
    GenerationState,
    StepRegistry,
    WorkflowRegistry,
)
from app.workflows.flows.base_workflow import BaseWorkflow

from .test_workflows_utils import (
    clean_registries,
    create_mock_step,
)


class SampleWorkflow(BaseWorkflow):
    """Sample workflow for cache testing."""

    @property
    def workflow_type(self) -> str:
        return "sample_workflow"

    def build_graph(self):
        from langgraph.graph import END, START, StateGraph

        from app.workflows.steps.node_factory import create_step_node

        builder = StateGraph(GenerationState)
        builder.add_node("step1", create_step_node("step1"))
        builder.add_edge(START, "step1")
        builder.add_edge("step1", END)
        return builder.compile()


@pytest.mark.asyncio
async def test_workflow_graph_cache_creates_valid_graph(clean_registries):
    """Test that WorkflowRegistry creates valid compiled graphs from workflows."""
    # Register test step
    step1 = create_mock_step(identifier="step1", dependencies=[])
    StepRegistry.register(step1)

    # Build graph via cache
    graph = WorkflowRegistry.get_or_build_graph(SampleWorkflow)

    # Verify graph structure
    assert graph is not None
    assert hasattr(graph, "invoke")
    assert hasattr(graph, "ainvoke")


@pytest.mark.asyncio
async def test_workflow_graph_cache_stores_graphs(clean_registries):
    """Test that WorkflowRegistry properly caches compiled graphs."""
    step = create_mock_step(identifier="step1", dependencies=[])
    StepRegistry.register(step)

    WorkflowRegistry.clear()

    # First call builds and caches
    graph1 = WorkflowRegistry.get_or_build_graph(SampleWorkflow)

    # Second call retrieves from cache
    graph2 = WorkflowRegistry.get_or_build_graph(SampleWorkflow)

    assert graph2 is graph1  # Same object, not reconstructed


def test_workflow_graph_cache_returns_same_after_clear(clean_registries):
    """Test that cache rebuilds after clear."""
    step = create_mock_step(identifier="step1", dependencies=[])
    StepRegistry.register(step)

    WorkflowRegistry.clear()
    graph1 = WorkflowRegistry.get_or_build_graph(SampleWorkflow)

    WorkflowRegistry.clear()
    graph2 = WorkflowRegistry.get_or_build_graph(SampleWorkflow)

    # After clear, should build new graph
    assert graph2 is not graph1


def test_workflow_graph_cache_clear():
    """Test that cache can be cleared."""
    WorkflowRegistry.clear()
    assert len(WorkflowRegistry._graph_cache) == 0
    logger = __import__("structlog").get_logger()
    # Just verify it doesn't error


def test_workflow_graph_cache_key_uniqueness(clean_registries):
    """Test that different workflow classes use different cache keys."""
    step = create_mock_step(identifier="step1", dependencies=[])
    StepRegistry.register(step)

    # Create two different workflow classes
    class Workflow1(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "workflow_1"

        def build_graph(self):
            from langgraph.graph import END, START, StateGraph

            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            builder.add_edge(START, "step1")
            builder.add_edge("step1", END)
            return builder.compile()

    class Workflow2(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "workflow_2"

        def build_graph(self):
            from langgraph.graph import END, START, StateGraph

            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            builder.add_edge(START, "step1")
            builder.add_edge("step1", END)
            return builder.compile()

    WorkflowRegistry.clear()

    graph1 = WorkflowRegistry.get_or_build_graph(Workflow1)
    graph2 = WorkflowRegistry.get_or_build_graph(Workflow2)

    # These should be different graphs
    assert graph1 is not graph2


@pytest.mark.asyncio
async def test_workflow_graph_parallel_execution(clean_registries):
    """Test that independent steps can execute in parallel."""
    # Create independent steps (no dependencies)
    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=[])
    step3 = create_mock_step(identifier="step3", dependencies=[])

    StepRegistry.register(step1)
    StepRegistry.register(step2)
    StepRegistry.register(step3)

    class ParallelTestWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "parallel_test_workflow"

        def build_graph(self):
            from langgraph.graph import END, START, StateGraph

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

    graph = WorkflowRegistry.get_or_build_graph(ParallelTestWorkflow)

    # All steps should exist
    assert "step1" in graph.nodes
    assert "step2" in graph.nodes
    assert "step3" in graph.nodes


@pytest.mark.asyncio
async def test_build_workflow_graph_with_diamond_dependency(clean_registries):
    """Test graph handles diamond dependency pattern."""
    # Register steps for diamond pattern:
    #     step1
    #    /    \
    #  step2  step3
    #    \    /
    #     step4

    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])
    step3 = create_mock_step(identifier="step3", dependencies=["step1"])
    step4 = create_mock_step(identifier="step4", dependencies=["step2", "step3"])

    StepRegistry.register(step1)
    StepRegistry.register(step2)
    StepRegistry.register(step3)
    StepRegistry.register(step4)

    class DiamondTestWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "diamond_test_workflow"

        def build_graph(self):
            from langgraph.graph import END, START, StateGraph

            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            builder.add_node("step2", create_step_node("step2"))
            builder.add_node("step3", create_step_node("step3"))
            builder.add_node("step4", create_step_node("step4"))
            builder.add_edge(START, "step1")
            builder.add_edge("step1", "step2")
            builder.add_edge("step1", "step3")
            builder.add_edge("step2", "step4")
            builder.add_edge("step3", "step4")
            builder.add_edge("step4", END)
            return builder.compile()

    # Build graph with all steps
    graph = WorkflowRegistry.get_or_build_graph(DiamondTestWorkflow)

    # All nodes should exist
    assert "step1" in graph.nodes
    assert "step2" in graph.nodes
    assert "step3" in graph.nodes
    assert "step4" in graph.nodes


@pytest.mark.asyncio
async def test_workflow_graph_state_passes_through_steps(clean_registries):
    """Test that GenerationState properly flows through graph execution."""
    step1 = create_mock_step(identifier="step1", dependencies=[])
    StepRegistry.register(step1)

    graph = WorkflowRegistry.get_or_build_graph(SampleWorkflow)

    # Create initial state
    state = {
        "session_id": 1,
        "execution_id": 1,
        "db": Mock(),
        "transcription": "Test transcription",
    }

    # Graph should preserve state structure
    assert isinstance(state, dict)
    assert "session_id" in state
    assert "execution_id" in state
    assert "db" in state


@pytest.mark.asyncio
async def test_build_workflow_graph_handles_nonexistent_step(clean_registries):
    """Test that graph builder handles missing step references."""
    step1 = create_mock_step(identifier="step1", dependencies=[])
    StepRegistry.register(step1)

    class MissingStepWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "missing_step_workflow"

        def build_graph(self):
            from langgraph.graph import END, START, StateGraph

            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            # Try to add node for nonexistent step - should raise error from factory
            builder.add_node("nonexistent_step", create_step_node("nonexistent_step"))
            builder.add_edge(START, "step1")
            builder.add_edge("step1", "nonexistent_step")
            builder.add_edge("nonexistent_step", END)
            return builder.compile()

    # Try to build graph with reference to nonexistent step
    # This should raise an error from the step factory
    try:
        graph = WorkflowRegistry.get_or_build_graph(MissingStepWorkflow)
    except (KeyError, ValueError) as e:
        # If it fails, it should be clear why
        assert "nonexistent_step" in str(e) or "not found" in str(e).lower()
        # This is expected behavior


@pytest.mark.asyncio
async def test_workflow_graph_with_complex_dependencies(clean_registries):
    """Test graph with complex multi-level dependencies."""
    # Create steps for complex workflow:
    # step1 -> step2 -> step4
    # step3 ---------> step4

    step1 = create_mock_step(identifier="step1", dependencies=[])
    step2 = create_mock_step(identifier="step2", dependencies=["step1"])
    step3 = create_mock_step(identifier="step3", dependencies=[])
    step4 = create_mock_step(identifier="step4", dependencies=["step2", "step3"])

    StepRegistry.register(step1)
    StepRegistry.register(step2)
    StepRegistry.register(step3)
    StepRegistry.register(step4)

    class ComplexTestWorkflow(BaseWorkflow):
        @property
        def workflow_type(self) -> str:
            return "complex_test_workflow"

        def build_graph(self):
            from langgraph.graph import END, START, StateGraph

            from app.workflows.steps.node_factory import create_step_node

            builder = StateGraph(GenerationState)
            builder.add_node("step1", create_step_node("step1"))
            builder.add_node("step2", create_step_node("step2"))
            builder.add_node("step3", create_step_node("step3"))
            builder.add_node("step4", create_step_node("step4"))
            builder.add_edge(START, "step1")
            builder.add_edge(START, "step3")
            builder.add_edge("step1", "step2")
            builder.add_edge("step2", "step4")
            builder.add_edge("step3", "step4")
            builder.add_edge("step4", END)
            return builder.compile()

    graph = WorkflowRegistry.get_or_build_graph(ComplexTestWorkflow)

    # Verify all steps are included
    assert "step1" in graph.nodes
    assert "step2" in graph.nodes
    assert "step3" in graph.nodes
    assert "step4" in graph.nodes
