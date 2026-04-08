"""Test utilities and fixtures for workflow testing."""

import asyncio
from typing import Any, ClassVar
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.workflows.steps.base_step import WorkflowStep


class MockStep(WorkflowStep):
    """Mock step for testing."""

    _identifier: str = "mock_step"
    _context_requirements: ClassVar[list[str]] = []
    _generate_result: ClassVar[dict[str, Any]] = {}

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def context_requirements(self) -> list[str]:
        return self._context_requirements

    async def _generate(self, session_id: int, _, context: dict[str, Any]) -> dict[str, Any]:
        """Return predefined result or raise error if configured."""
        if callable(self._generate_result):
            return self._generate_result(session_id, context)
        return self._generate_result


def create_mock_step(
    identifier: str,
    context_requirements: list[str] | None = None,
    generate_result: dict[str, Any] | None = None,
) -> MockStep:
    """
    Create a mock step for testing.

    Args:
        identifier: Step identifier
        context_requirements: List of required context keys this step needs
        generate_result: Dict with "content", "content_type", "meta_info"

    Returns:
        MockStep instance
    """
    step = MockStep()
    step._identifier = identifier
    step._context_requirements = context_requirements or []
    step._generate_result = generate_result or {
        "content": f"Mock content from {identifier}",
        "content_type": "plain_text",
        "meta_info": {"type": "mock"},
    }
    return step


# NOTE: mock_db_session, mock_session_model, and clean_registries fixtures
# are defined in conftest.py to serve as central fixtures for all tests.
# They are automatically available to all test modules.


def create_test_workflow(workflow_name: str, step_ids: list[str]):
    """
    Create a simple test workflow class.

    Useful for tests that don't need a real workflow implementation.

    Args:
        workflow_name: Name of the workflow
        step_ids: List of step identifiers this workflow uses

    Returns:
        A BaseWorkflow subclass ready to be registered
    """
    from langgraph.graph import END, START, StateGraph

    from app.workflows.flows.base_workflow import BaseWorkflow
    from app.workflows.steps.node_factory import create_step_node

    class TestWorkflow(BaseWorkflow):
        # Store step IDs for test introspection
        _test_step_ids = step_ids

        @property
        def workflow_type(self) -> str:
            return workflow_name

        def build_graph(self):
            builder = StateGraph(dict)
            # Add all steps as nodes
            for step_id in step_ids:
                builder.add_node(step_id, create_step_node(step_id))

            # Connect first step to START
            if step_ids:
                builder.add_edge(START, step_ids[0])
                # Chain steps together
                for i in range(len(step_ids) - 1):
                    builder.add_edge(step_ids[i], step_ids[i + 1])
                # Connect last step to END
                builder.add_edge(step_ids[-1], END)

            return builder.compile()

    return TestWorkflow


def create_generation_state(
    session_id: int = 1,
    execution_id: int = 1,
    db_session: Session = None,
    transcription: str = "Test transcription",
    **kwargs,
) -> dict[str, Any]:
    """
    Create a test workflow state dict.

    Args:
        session_id: Session ID
        execution_id: Execution ID
        db_session: Database session (mock if None)
        transcription: Transcription text
        **kwargs: Additional state fields

    Returns:
        State dict for workflow execution
    """
    if db_session is None:
        db_session = Mock(spec=Session)

    state: dict[str, Any] = {
        "session_id": session_id,
        "execution_id": execution_id,
        "db": db_session,
        "transcription": transcription,
    }

    # Add any additional kwargs
    for key, value in kwargs.items():
        state[key] = value  # type: ignore

    return state


class AsyncContextMock:
    """Helper for mocking async functions."""

    def __init__(self, return_value=None, side_effect=None):
        self.return_value = return_value
        self.side_effect = side_effect

    async def __call__(self, *args, **kwargs):
        if self.side_effect:
            if isinstance(self.side_effect, Exception):
                raise self.side_effect
            return self.side_effect(*args, **kwargs)
        return self.return_value


def patch_llm():
    """
    Context manager to patch LLM calls in steps.

    Usage:
        with patch_llm() as mock_llm:
            mock_llm.return_value.content = "Generated content"
            # run test
    """
    return patch("app.workflows.steps.base_step.WorkflowStep.get_model")


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
