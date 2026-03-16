"""Flows package - workflow implementations.

This package contains all workflow implementations. Each workflow:
- Inherits from BaseWorkflow
- Implements build_graph() to define LangGraph orchestration
- Auto-registers itself on import
"""

import structlog

from app.workflows.execution_context import WorkflowRegistry
from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.flows.talk_workflow import TalkWorkflow

logger = structlog.get_logger()

# Auto-register workflows on import
WorkflowRegistry.register_workflow_class("talk_workflow", TalkWorkflow)
logger.info("talk_workflow_registered")


__all__ = [
    "BaseWorkflow",
    "TalkWorkflow",
]
