"""Generative workflows package - LangGraph-based execution with self-contained steps.

Architecture:
- BaseWorkflow: Abstract base class for LangGraph workflow definitions (in flows/)
- ExecutionContext: State management and registries for steps/workflows
- Steps: Individual step implementations (auto-registered)
  - LLMStep: Base class for prompt-based steps
  - node_factory.py: Factory for creating LangGraph nodes from steps
- Flows: Workflow implementations (auto-registered)
  - BaseWorkflow: Abstract workflow base class
  - TalkWorkflow: Example workflow
- Services: Business logic layer

Workflow Definition:
Workflows inherit from BaseWorkflow and implement build_graph() to define their
own LangGraph orchestration. This allows use of any LangGraph pattern:
- Sequential execution (deterministic workflows)
- Parallelization (independent subtasks)
- Routing (conditional edges based on state)
- Orchestrator-worker (dynamic task creation with Send)
- Evaluator-optimizer (feedback loops)

The node_factory module provides create_step_node() to eliminate code duplication
when building workflows with multiple steps.

Example:
```python
from langgraph.graph import StateGraph, START, END
from app.workflows.flows import BaseWorkflow
from app.workflows.steps.node_factory import create_step_node

class MyWorkflow(BaseWorkflow):
    @property
    def workflow_type(self) -> str:
        return "my_workflow"

    def build_graph(self):
        builder = StateGraph(dict)  # Use free-form dict, no TypedDict needed
        builder.add_node("step1", create_step_node("step1"))
        builder.add_node("step2", create_step_node("step2"))
        # Add edges...
        return builder.compile()
```
"""

# Import base class and workflows from flows package
# Import execution context components for public API
from app.workflows.execution_context import (
    StepRegistry,
    WorkflowRegistry,
    is_workflow_target,
    resolve_target_to_workflow_class,
)
from app.workflows.flows import BaseWorkflow, TalkWorkflow

# Import service layer
from app.workflows.services.execution_service import WorkflowExecutionService

# Import steps first to trigger auto-registration
from app.workflows.steps import WorkflowStep

__all__ = [
    # Base class and workflows
    "BaseWorkflow",
    "TalkWorkflow",
    # Steps
    "WorkflowStep",
    # Registry and execution context
    "StepRegistry",
    "WorkflowRegistry",
    "is_workflow_target",
    "resolve_target_to_workflow_class",
    # Services
    "WorkflowExecutionService",
]
