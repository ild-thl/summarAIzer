"""Initialization for workflow execution context."""

import structlog

logger = structlog.get_logger()


def initialize_workflows():
    """
    Initialize workflow execution system.

    This function ensures all steps and workflows are registered and ready
    for execution. Call this at application startup.

    Uses lazy imports to avoid circular import issues during startup.
    """
    logger.info("initializing_workflow_execution_system")

    # Lazy imports to avoid circular imports at module load time
    from app.workflows import steps
    from app.workflows.execution_context import StepRegistry, WorkflowRegistry
    from app.workflows.flows import TalkWorkflow

    # Log registered steps
    registered_steps = StepRegistry.get_all_steps()
    logger.info(
        "registered_steps_summary",
        count=len(registered_steps),
        step_names=list(registered_steps.keys()),
    )

    # Log registered workflow classes
    registered_workflow_classes = WorkflowRegistry.get_all_workflow_classes()
    logger.info(
        "registered_workflow_classes_summary",
        count=len(registered_workflow_classes),
        workflow_names=list(registered_workflow_classes.keys()),
        workflow_classes=[cls.__name__ for cls in registered_workflow_classes.values()],
    )

    logger.info("workflow_execution_system_initialized")
