"""Execution context and state management for workflow execution with LangGraph."""

from typing import Any, ClassVar, TypedDict

import structlog

logger = structlog.get_logger()


class GenerationState(TypedDict, total=False):
    """
    Shared state dict for LangGraph workflow execution.

    Contains both inputs (session_id, transcription) and outputs (generated content).
    Steps update this state as they execute, allowing downstream steps to access results.

    Transcription is optional - steps define their own dependencies:
    - SummaryStep requires transcription and will fail if not available
    - TagsStep uses transcription if available, falls back to session.short_description

    Note: Database session is not included in state as it's not serializable.
    Steps create their own Session instances when needed using SessionLocal().
    """

    # Execution context (set once at start)
    session_id: int
    execution_id: int

    # Input data (optional - steps define their own dependencies)
    transcription: str | None

    # Generated content (populated by steps)
    summary: str
    key_takeaways: str
    tags: str


class StepRegistry:
    """
    Registry for discovering available steps and their dependencies.

    Steps auto-register themselves with the registry, making them discoverable
    and allowing the workflow to build execution plans based on step identifiers.
    """

    _steps: ClassVar[dict[str, Any]] = {}
    _step_dependencies: ClassVar[dict[str, int]] = {}

    @classmethod
    def register(cls, step_instance: Any) -> None:
        """
        Register a step instance.

        Args:
            step_instance: Instance of WorkflowStep subclass
        """
        identifier = step_instance.identifier
        cls._steps[identifier] = step_instance
        cls._step_dependencies[identifier] = step_instance.dependencies

        logger.info(
            "step_registered",
            identifier=identifier,
            dependencies=step_instance.dependencies,
        )

    @classmethod
    def get_step(cls, identifier: str) -> Any:
        """
        Get a step instance by identifier.

        Args:
            identifier: Step identifier (e.g., "summary", "tags")

        Returns:
            Step instance

        Raises:
            ValueError: If step not found
        """
        if identifier not in cls._steps:
            raise ValueError(f"Step '{identifier}' not registered")
        return cls._steps[identifier]

    @classmethod
    def get_dependencies(cls, identifier: str) -> list[str]:
        """
        Get dependencies for a step.

        Args:
            identifier: Step identifier

        Returns:
            List of step identifiers this step depends on

        Raises:
            ValueError: If step not found
        """
        if identifier not in cls._step_dependencies:
            raise ValueError(f"Step '{identifier}' not registered")
        return cls._step_dependencies[identifier]

    @classmethod
    def get_all_steps(cls) -> dict[str, Any]:
        """
        Get all registered steps.

        Returns:
            Dict mapping identifier → step instance
        """
        return cls._steps.copy()

    @classmethod
    def resolve_execution_order(cls, step_ids: list[str]) -> list[str]:
        """
        Resolve execution order for a set of steps based on dependencies.

        Uses topological sort to determine the correct execution order.

        Args:
            step_ids: List of step identifiers to execute

        Returns:
            List of step identifiers in execution order

        Raises:
            ValueError: If there are circular dependencies or unknown steps
        """
        # Validate all steps exist
        for step_id in step_ids:
            if step_id not in cls._steps:
                raise ValueError(f"Step '{step_id}' not registered")

        # Build adjacency list for steps in this execution
        dependencies = {}
        for step_id in step_ids:
            deps = cls._step_dependencies[step_id]
            # Only include dependencies that are in our execution set
            dependencies[step_id] = [d for d in deps if d in step_ids]

        # Topological sort (Kahn's algorithm)
        in_degree = {step_id: len(dependencies[step_id]) for step_id in step_ids}
        queue = [step_id for step_id in step_ids if in_degree[step_id] == 0]
        result = []

        while queue:
            # Sort for deterministic order when multiple items have 0 in-degree
            queue.sort()
            current = queue.pop(0)
            result.append(current)

            # Reduce in-degree for dependent steps
            for step_id in step_ids:
                if current in dependencies[step_id]:
                    in_degree[step_id] -= 1
                    if in_degree[step_id] == 0:
                        queue.append(step_id)

        # Check for cycles
        if len(result) != len(step_ids):
            raise ValueError("Circular dependencies detected in workflow")

        return result

    @classmethod
    def clear(cls) -> None:
        """Clear all registered steps (useful for testing)."""
        cls._steps.clear()
        cls._step_dependencies.clear()


class WorkflowRegistry:
    """
    Unified registry for workflows and their compiled graphs.

    Manages both workflow class discovery and graph caching.
    All workflows must be BaseWorkflow classes that define their own LangGraph orchestration.
    """

    _workflow_classes: ClassVar[dict[str, Any]] = {}
    _graph_cache: ClassVar[dict[str, Any]] = {}  # Caches compiled graphs to avoid rebuilding

    @classmethod
    def register_workflow_class(cls, workflow_name: str, workflow_class: Any) -> None:
        """
        Register a workflow class that defines its own LangGraph.

        Args:
            workflow_name: Name of the workflow (e.g., "talk_workflow")
            workflow_class: BaseWorkflow subclass that implements build_graph()
        """
        cls._workflow_classes[workflow_name] = workflow_class
        logger.info(
            "workflow_class_registered",
            workflow_name=workflow_name,
            workflow_class=workflow_class.__name__,
        )

    @classmethod
    def get_workflow_class(cls, workflow_name: str) -> Any:
        """
        Get workflow class for a workflow.

        Args:
            workflow_name: Name of the workflow

        Returns:
            BaseWorkflow subclass

        Raises:
            ValueError: If workflow not found
        """
        if workflow_name not in cls._workflow_classes:
            raise ValueError(f"Workflow class '{workflow_name}' not registered")
        return cls._workflow_classes[workflow_name]

    @classmethod
    def get_or_build_graph(cls, workflow_class: Any) -> Any:
        """
        Get a cached workflow graph or build and cache a new one.

        Avoids expensive graph compilation on every execution.

        Args:
            workflow_class: BaseWorkflow subclass or instance

        Returns:
            Compiled LangGraph StateGraph ready for execution
        """
        # Check if it's a workflow instance or class
        if isinstance(workflow_class, type):
            # It's a class, instantiate it
            workflow_instance = workflow_class()
            workflow_name = workflow_class.__name__
        else:
            # It's already an instance
            workflow_instance = workflow_class
            workflow_name = workflow_instance.workflow_type

        # Use workflow_type as cache key for proper cache isolation
        # This ensures SingleStepWorkflow instances with different targets
        # don't share cached graphs
        cache_key = workflow_instance.workflow_type

        if cache_key not in cls._graph_cache:
            logger.info(
                "building_and_caching_workflow_graph",
                workflow_name=cache_key,
                workflow_class=workflow_name,
            )
            cls._graph_cache[cache_key] = workflow_instance.build_graph()

        return cls._graph_cache[cache_key]

    @classmethod
    def is_workflow(cls, name: str) -> bool:
        """
        Check if a name is a registered workflow.

        Args:
            name: Name to check

        Returns:
            True if registered workflow, False otherwise
        """
        return name in cls._workflow_classes

    @classmethod
    def get_all_workflow_classes(cls) -> dict[str, Any]:
        """
        Get all registered workflow classes.

        Returns:
            Dict mapping workflow_name → workflow_class
        """
        return cls._workflow_classes.copy()

    @classmethod
    def clear(cls) -> None:
        """Clear all registered workflows and graphs (useful for testing)."""
        cls._workflow_classes.clear()
        cls._graph_cache.clear()


def is_workflow_target(target: str) -> bool:
    """
    Check if target is a registered workflow.

    Args:
        target: Either a workflow name (e.g., "talk_workflow") or step identifier (e.g., "summary")

    Returns:
        True if target is a registered workflow, False if it's a step

    Raises:
        ValueError: If target is neither workflow nor step
    """
    # Check if it's a workflow
    if WorkflowRegistry.is_workflow(target):
        return True

    # Check if it's a step (valid target but not a workflow)
    try:
        StepRegistry.get_step(target)
        return False
    except ValueError:
        pass

    raise ValueError(f"Unknown target: '{target}'. Not a registered workflow or step.")


def resolve_target_to_workflow_class(target: str) -> Any:
    """
    Resolve a target to a workflow class.

    Args:
        target: Either a workflow name (e.g., "talk_workflow") or step identifier (e.g., "summary")

    Returns:
        If workflow class registered: workflow class
        If step: a synthetic workflow class that executes just that step

    Raises:
        ValueError: If target is not recognized
    """
    logger.info(
        "resolving_target_to_workflow_class",
        target=target,
    )

    # Check if it's a workflow class (primary path)
    if WorkflowRegistry.is_workflow(target):
        workflow_class = WorkflowRegistry.get_workflow_class(target)
        logger.info(
            "target_is_registered_workflow",
            target=target,
            workflow_class=workflow_class.__name__,
        )
        return workflow_class

    # For backward compatibility: check if it's a step
    try:
        step = StepRegistry.get_step(target)
        logger.info(
            "target_is_registered_step_creating_synthetic_workflow",
            target=target,
            step_identifier=step.identifier,
        )

        # Return a synthetic workflow that just runs this single step
        # This allows old code that triggers individual steps to still work
        from app.workflows.flows.base_workflow import BaseWorkflow

        class SingleStepWorkflow(BaseWorkflow):
            @property
            def workflow_type(self) -> str:
                return target

            def build_graph(self):
                from langgraph.graph import END, START, StateGraph

                logger.info(
                    "building_single_step_workflow_graph",
                    target=target,
                    step_identifier=target,
                )

                async def step_node(state: GenerationState) -> dict[str, str]:
                    step = StepRegistry.get_step(target)
                    context = {
                        k: v for k, v in state.items() if k not in ["session_id", "execution_id"]
                    }
                    return await step.execute(
                        session_id=state["session_id"],
                        execution_id=state["execution_id"],
                        context=context,
                    )

                builder = StateGraph(GenerationState)
                # Use wrapper node name to avoid conflicting with state channel names
                # The node's return value will update the correct state fields
                node_name = f"_execute_{target}"
                builder.add_node(node_name, step_node)
                builder.add_edge(START, node_name)
                builder.add_edge(node_name, END)
                return builder.compile()

        logger.info(
            "synthetic_workflow_class_created",
            target=target,
        )
        return SingleStepWorkflow
    except ValueError as step_lookup_error:
        logger.error(
            "target_not_found_in_step_registry",
            target=target,
            error=str(step_lookup_error),
        )

    raise ValueError(f"Unknown target: '{target}'. Not a registered workflow or step.")
