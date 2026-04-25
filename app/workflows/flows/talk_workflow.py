"""TalkWorkflow - Main workflow for talk/session content generation using LangGraph."""

from typing import Annotated

import structlog
from langgraph.graph import END, START, StateGraph

from app.crud import generated_content as content_crud
from app.database.connection import SessionLocal
from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.steps.node_factory import create_step_node

logger = structlog.get_logger()


def merge_dicts(left: dict, right: dict) -> dict:
    """
    Reducer function to merge state updates from parallel steps.

    When multiple steps execute in parallel, LangGraph calls this reducer
    to merge their individual updates into the combined state.

    Args:
        left: Existing state
        right: Update from a step

    Returns:
        Merged state with right's values updated into left
    """
    return {**left, **right}


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

    def _get_existing_transcription(self, session_id: int | None) -> str | None:
        """Return persisted transcription content for the session, if available."""
        if session_id is None:
            return None

        db = SessionLocal()
        try:
            existing_transcription = content_crud.get_content_by_identifier(
                db, session_id, "transcription"
            )
            if not existing_transcription:
                return None
            return existing_transcription.content
        finally:
            db.close()

    def build_graph(self):
        """
        Build the LangGraph StateGraph for this workflow.

        Defines nodes (created from steps via node factory) and edges for orchestration.
        Includes conditional routing to skip transcription step if transcription already exists.

        Execution flow:
        - If transcription exists: START → _load_existing_transcription → key_takeaways, tags → summary
        - If transcription doesn't exist: START → transcription → key_takeaways, tags → summary

        This preserves the ability to trigger transcription step directly, which always runs.
        Only within talk_workflow do we conditionally skip unnecessary transcription.

        Returns:
            Compiled LangGraph StateGraph ready to invoke
        """
        logger.info("building_talk_workflow_graph")

        # Create the state graph with Annotated reducer for parallel step merging
        # The merge_dicts reducer allows multiple parallel steps to update state simultaneously
        builder = StateGraph(Annotated[dict, merge_dicts])

        # Create nodes from steps using node factory
        # This provides consistent logging and error handling
        builder.add_node("transcription", create_step_node("transcription"))
        builder.add_node("summary", create_step_node("summary"))
        builder.add_node("key_takeaways", create_step_node("key_takeaways"))
        builder.add_node("tags", create_step_node("tags"))

        # Hydrate persisted transcription into graph state when we skip the step.
        async def load_existing_transcription(state: dict) -> dict:
            """Load persisted transcription into state for downstream steps."""
            if state.get("transcription"):
                return {"transcription": state["transcription"]}

            session_id = state.get("session_id")
            transcription = self._get_existing_transcription(session_id)
            if not transcription:
                raise ValueError(
                    f"Expected existing transcription for session {session_id}, but none was found"
                )

            logger.info(
                "talk_workflow_loaded_existing_transcription",
                session_id=session_id,
                char_count=len(transcription),
            )
            return {"transcription": transcription}

        builder.add_node("_load_existing_transcription", load_existing_transcription)

        # Conditional routing from START based on whether transcription already exists.
        def route_from_start(state: dict) -> str:
            """Route START based on transcription existence in database."""
            session_id = state.get("session_id")
            if state.get("transcription") or self._get_existing_transcription(session_id):
                logger.info(
                    "talk_workflow_skipping_transcription_already_exists",
                    session_id=session_id,
                    has_transcription=True,
                )
                return "_load_existing_transcription"

            logger.info("talk_workflow_routing_to_transcription", session_id=session_id)
            return "transcription"

        builder.add_conditional_edges(START, route_from_start)

        # Both transcription and the hydrated skip path route to parallel key_takeaways and tags.
        builder.add_edge("transcription", "key_takeaways")
        builder.add_edge("transcription", "tags")
        builder.add_edge("_load_existing_transcription", "key_takeaways")
        builder.add_edge("_load_existing_transcription", "tags")

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
