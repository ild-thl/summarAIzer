"""TalkWorkflow - Main workflow for talk/session content generation using LangGraph."""

import structlog
from langgraph.graph import END, START, StateGraph

from app.workflows.execution_context import GenerationState
from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.steps.node_factory import create_step_node

logger = structlog.get_logger()


class TalkWorkflow(BaseWorkflow):
    """
    Main workflow for generating session content using LangGraph.

    Orchestration:
    1. Key Takeaways (independent) - extracts actionable points from transcript
    2. Tags (independent) - categorizes session
    3. Summary (independent, but waits for key_takeaways & tags in workflow) - generates overview

    Execution flow:
    - Key Takeaways and Tags start in parallel (both independent)
    - Summary waits for both to complete, so it can optionally use their results in context
    - Summary can run standalone without key_takeaways/tags having completed first
    - All steps handle their own content generation and database persistence

    Steps are created using the node factory, which provides consistent logging
    and error handling for all step nodes.
    """

    @property
    def workflow_type(self) -> str:
        """Workflow identifier."""
        return "talk_workflow"

    def build_graph(self):
        """
        Build the LangGraph StateGraph for this workflow.

        Defines nodes (created from steps via node factory) and edges for orchestration.
        Returns a compiled graph ready for execution.

        Returns:
            Compiled LangGraph StateGraph ready to invoke
        """
        logger.info("building_talk_workflow_graph")

        # Create the state graph
        builder = StateGraph(GenerationState)

        # Create nodes from steps using node factory
        # This provides consistent logging and error handling
        builder.add_node("summary", create_step_node("summary"))
        builder.add_node("key_takeaways", create_step_node("key_takeaways"))
        builder.add_node("tags", create_step_node("tags"))

        # Define execution order with edges
        # Key Takeaways and Tags start in parallel (both independent)
        builder.add_edge(START, "key_takeaways")
        builder.add_edge(START, "tags")

        # Summary waits for both key_takeaways and tags to complete,
        # so it can optionally use their results in context
        builder.add_edge("key_takeaways", "summary")
        builder.add_edge("tags", "summary")

        # Summary completes the workflow
        builder.add_edge("summary", END)

        # Compile the graph
        graph = builder.compile()

        logger.info(
            "talk_workflow_graph_built_successfully",
            workflow_type=self.workflow_type,
        )

        return graph
