"""Test utilities and fixtures for workflow testing."""

import asyncio
from typing import Dict, Any, List
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from sqlalchemy.orm import Session
import pytest

from app.workflows.execution_context import GenerationState, StepRegistry, WorkflowRegistry
from app.workflows.steps.base_step import WorkflowStep
from app.database.models import Session as SessionModel


class MockStep(WorkflowStep):
    """Mock step for testing."""
    
    _identifier: str = "mock_step"
    _dependencies: List[str] = []
    _generate_result: Dict[str, Any] = {}
    
    @property
    def identifier(self) -> str:
        return self._identifier
    
    @property
    def dependencies(self) -> List[str]:
        return self._dependencies
    
    async def _generate(
        self, session_id: int, db: Session, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Return predefined result or raise error if configured."""
        if callable(self._generate_result):
            return self._generate_result(session_id, context)
        return self._generate_result


def create_mock_step(
    identifier: str,
    dependencies: List[str] = None,
    generate_result: Dict[str, Any] = None,
) -> MockStep:
    """
    Create a mock step for testing.
    
    Args:
        identifier: Step identifier
        dependencies: List of dependencies
        generate_result: Dict with "content", "content_type", "meta_info"
        
    Returns:
        MockStep instance
    """
    step = MockStep()
    step._identifier = identifier
    step._dependencies = dependencies or []
    step._generate_result = generate_result or {
        "content": f"Mock content from {identifier}",
        "content_type": "plain_text",
        "meta_info": {"type": "mock"},
    }
    return step


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    db = Mock(spec=Session)
    db.query = Mock()
    db.add = Mock()
    db.commit = Mock()
    db.rollback = Mock()
    
    # Configure refresh to assign an ID if the object doesn't have one
    def refresh_side_effect(obj):
        if hasattr(obj, 'id') and obj.id is None:
            obj.id = 1  # Assign mock ID
    
    db.refresh = Mock(side_effect=refresh_side_effect)
    return db


@pytest.fixture
def mock_session_model():
    """Create a mock Session database model."""
    session = Mock(spec=SessionModel)
    session.id = 1
    session.title = "Test Session"
    session.speakers = ["Speaker 1", "Speaker 2"]
    session.categories = ["Category 1"]
    session.duration = 60
    return session


@pytest.fixture
def clean_registries():
    """Clear and restore registries for testing."""
    # Save original state
    original_steps = StepRegistry.get_all_steps().copy()
    original_workflow_classes = WorkflowRegistry.get_all_workflow_classes().copy()
    
    # Clear registries
    StepRegistry.clear()
    WorkflowRegistry.clear()
    
    yield
    
    # Restore original state
    StepRegistry.clear()
    WorkflowRegistry.clear()
    
    # Re-register original steps
    for step_id, step in original_steps.items():
        StepRegistry.register(step)
    
    # Re-register original workflow classes
    for workflow_name, workflow_class in original_workflow_classes.items():
        WorkflowRegistry.register_workflow_class(workflow_name, workflow_class)


def create_test_workflow(workflow_name: str, step_ids: List[str]):
    """
    Create a simple test workflow class.
    
    Useful for tests that don't need a real workflow implementation.
    
    Args:
        workflow_name: Name of the workflow
        step_ids: List of step identifiers this workflow uses
        
    Returns:
        A BaseWorkflow subclass ready to be registered
    """
    from app.workflows.flows.base_workflow import BaseWorkflow
    from langgraph.graph import StateGraph, START, END
    from app.workflows.steps.node_factory import create_step_node
    
    class TestWorkflow(BaseWorkflow):
        # Store step IDs for test introspection
        _test_step_ids = step_ids
        
        @property
        def workflow_type(self) -> str:
            return workflow_name
        
        def build_graph(self):
            builder = StateGraph(GenerationState)
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
    **kwargs
) -> GenerationState:
    """
    Create a test GenerationState.
    
    Args:
        session_id: Session ID
        execution_id: Execution ID
        db_session: Database session (mock if None)
        transcription: Transcription text
        **kwargs: Additional state fields
        
    Returns:
        GenerationState dict
    """
    if db_session is None:
        db_session = Mock(spec=Session)
    
    state: GenerationState = GenerationState(
        session_id=session_id,
        execution_id=execution_id,
        db=db_session,
        transcription=transcription,
    )
    
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
    return patch('app.workflows.steps.base_step.WorkflowStep.get_model')


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
