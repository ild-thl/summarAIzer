"""Factory for creating LangGraph nodes from steps.

This module provides utilities to convert workflow steps into LangGraph node functions.
Node functions handle extracting context from state, executing the step, and managing
logging and error handling.
"""

from collections.abc import Callable

import structlog

from app.workflows.execution_context import GenerationState, StepRegistry

logger = structlog.get_logger()


def create_step_node(step_identifier: str) -> Callable:
    """
    Create a LangGraph node function for a given step.

    The returned node function:
    1. Retrieves the step from the registry
    2. Builds context from the current state
    3. Executes the step (which handles content generation and persistence)
    4. Returns the result to update the graph state
    5. Handles logging and errors

    This factory eliminates code duplication when defining workflows with multiple steps.

    Args:
        step_identifier: Step identifier (e.g., "summary", "tags", "key_takeaways")

    Returns:
        An async function suitable for use as a LangGraph node

    Raises:
        ValueError: If step is not registered
    """
    # Validate step exists at creation time
    step = StepRegistry.get_step(step_identifier)

    async def step_node(state: GenerationState) -> dict[str, str]:
        """
        Execute a step and update state with result.

        Args:
            state: Current execution state with session_id, execution_id, and context

        Returns:
            Dict with step_identifier: content to update the state
        """
        try:
            logger.info(
                "step_node_starting",
                step_identifier=step_identifier,
                session_id=state.get("session_id"),
                execution_id=state.get("execution_id"),
            )

            # Build context for this step from state, excluding execution metadata
            context = {k: v for k, v in state.items() if k not in ["session_id", "execution_id"]}

            # Validate that db was never added to state
            if "db" in context:
                logger.error(
                    "invalid_context_contains_db",
                    step_identifier=step_identifier,
                    db_type=type(context.get("db")).__name__,
                    db_value=repr(context.get("db"))[:100],
                )
                raise ValueError(
                    f"BUG: Database session was in context for step '{step_identifier}'. "
                    "This indicates state serialization/deserialization. "
                    "Steps should create their own SessionLocal() instances."
                )

            # Execute step (handles both content generation AND persistence)
            # Note: Step creates its own database session
            result = await step.execute(
                session_id=state["session_id"],
                execution_id=state["execution_id"],
                context=context,
            )

            logger.info(
                "step_node_completed",
                step_identifier=step_identifier,
                session_id=state.get("session_id"),
                execution_id=state.get("execution_id"),
            )

            # Result is dict like {identifier: content_string}
            # Return it to update state
            return result

        except Exception as e:
            logger.error(
                "step_node_failed",
                step_identifier=step_identifier,
                session_id=state.get("session_id"),
                execution_id=state.get("execution_id"),
                error=str(e),
                exc_info=True,
            )
            raise

    return step_node
