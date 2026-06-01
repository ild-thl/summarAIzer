"""TalkWorkflow - Format-aware documentation workflow using LangGraph."""

from typing import Annotated

import structlog
from langgraph.graph import END, START, StateGraph

from app.crud import generated_content as content_crud
from app.database.connection import SessionLocal
from app.database.models import Session as SessionModel
from app.database.models import SessionFormat
from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.steps.node_factory import create_step_node
from app.workflows.steps.sondercluster_step import SONDERCLUSTER_TAG_MAP

logger = structlog.get_logger()


def merge_dicts(left: dict, right: dict) -> dict:
    """
    Reducer function to merge state updates from parallel steps.

    When multiple steps execute in parallel, LangGraph calls this reducer
    to merge their individual updates into the combined state.
    """
    return {**left, **right}


# Format groups used for routing decisions
_DISCUSSION_FORMATS = {SessionFormat.DISCUSSION}
_WORKSHOP_FORMATS = {SessionFormat.WORKSHOP, SessionFormat.TRAINING, SessionFormat.LAB}
_LIGHTNING_FORMAT = SessionFormat.LIGHTNING_TALK
_INPUT_FORMAT = SessionFormat.INPUT


class TalkWorkflow(BaseWorkflow):
    """
    Format-aware documentation workflow for conference/education sessions.

    All paths:
        (transcription || slide_markdown) → (key_takeaways OR positions) → summary
        → [format-specific parallel end steps] → END

    End steps by format (all run in parallel after summary):

    Input (long talk):
        → quotes + mermaid + qna + glossary + tags + image

    Lightning Talk (short):
        → glossary + tags + image

    Workshop / Training / Lab:
        → glossary + tags + image

    Discussion / Panel:
        → quotes + qna + glossary + tags + image

    Other / unknown:
        → glossary + tags + image

    The transcription step is skipped when a transcription already exists in the database.
    Session format is loaded from the database and stored in state to drive routing.
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
            existing = content_crud.get_content_by_identifier(db, session_id, "transcription")
            if not existing:
                return None
            return existing.content
        finally:
            db.close()

    def _get_session_format(self, session_id: int | None) -> SessionFormat | None:
        """Return the session format from the database."""
        if session_id is None:
            return None

        db = SessionLocal()
        try:
            session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
            return session.session_format if session else None
        finally:
            db.close()

    def _get_session_tags(self, session_id: int | None) -> list[str]:
        """Return the session tags from the database."""
        if session_id is None:
            return []

        db = SessionLocal()
        try:
            session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
            return list(session.tags or []) if session else []
        finally:
            db.close()

    async def _load_existing_transcription_node(self, state: dict) -> dict:
        """Hydrate persisted transcription into graph state."""
        if state.get("transcription"):
            return {"transcription": state["transcription"]}

        session_id = state.get("session_id")
        transcription = self._get_existing_transcription(session_id)
        if not transcription:
            raise ValueError(
                f"Expected existing transcription for session {session_id}, but none found"
            )

        logger.info(
            "talk_workflow_loaded_existing_transcription",
            session_id=session_id,
            char_count=len(transcription),
        )
        return {"transcription": transcription}

    async def _init_session_context_node(self, state: dict) -> dict:
        """Load session format and tags into state for format-aware routing."""
        session_id = state.get("session_id")
        fmt = self._get_session_format(session_id)
        tags = self._get_session_tags(session_id)
        logger.info(
            "talk_workflow_session_context_loaded",
            session_id=session_id,
            session_format=fmt.value if fmt else None,
            session_tags=tags,
        )
        return {
            "session_format": fmt.value if fmt else None,
            "session_tags": tags,
        }

    def _parse_session_format(self, fmt_value: str | None) -> SessionFormat | None:
        """Parse session format value from state into enum if valid."""
        try:
            return SessionFormat(fmt_value) if fmt_value else None
        except ValueError:
            return None

    def _route_from_start(self, state: dict) -> str:
        """Skip transcription step when a transcription already exists."""
        session_id = state.get("session_id")
        if state.get("transcription") or self._get_existing_transcription(session_id):
            logger.info("talk_workflow_skipping_transcription", session_id=session_id)
            return "_load_existing_transcription"

        logger.info("talk_workflow_routing_to_transcription", session_id=session_id)
        return "transcription"

    def _route_after_init(self, state: dict) -> str:
        """Route to the content-extraction step for this format."""
        fmt_value = state.get("session_format")
        fmt = self._parse_session_format(fmt_value)

        if fmt in _DISCUSSION_FORMATS:
            logger.info("talk_workflow_discussion_path", session_format=fmt_value)
            return "positions"

        logger.info("talk_workflow_talk_path", session_format=fmt_value)
        return "key_takeaways"

    async def _transcription_ready_node(self, _state: dict) -> dict:
        """Join node for both transcription sources (generated vs. pre-existing)."""
        return {}

    async def _sources_ready_node(self, _state: dict) -> dict:
        """Barrier node waiting for transcription, slide markdown, and session context."""
        return {}

    def _route_after_summary(self, state: dict) -> list[str]:
        """Fan out to all parallel end steps appropriate for this format."""
        fmt_value = state.get("session_format")
        fmt = self._parse_session_format(fmt_value)

        if fmt in _DISCUSSION_FORMATS:
            logger.info("talk_workflow_post_summary_discussion", session_format=fmt_value)
            steps = ["quotes", "qna", "glossary", "tags", "image", "wordcloud"]
        elif fmt == _INPUT_FORMAT:
            logger.info("talk_workflow_post_summary_input_talk", session_format=fmt_value)
            steps = ["quotes", "mermaid", "qna", "glossary", "tags", "image", "wordcloud"]
        elif fmt in _WORKSHOP_FORMATS:
            logger.info("talk_workflow_post_summary_workshop", session_format=fmt_value)
            steps = ["glossary", "tags", "image", "wordcloud"]
        else:
            # LIGHTNING_TALK, OTHER, None
            logger.info("talk_workflow_post_summary_end", session_format=fmt_value)
            steps = ["glossary", "tags", "image", "wordcloud"]

        # Conditionally add Sondercluster step when session carries a cluster tag.
        session_tags = state.get("session_tags") or []
        if any(t.lower().strip() in SONDERCLUSTER_TAG_MAP for t in session_tags):
            logger.info(
                "talk_workflow_sondercluster_triggered",
                session_tags=session_tags,
            )
            steps.append("sondercluster")

        return steps

    def build_graph(self):
        """
        Build the LangGraph StateGraph for this workflow.

        Execution pattern:
        - Phase 1: Transcription (or load from DB), slide_markdown, and session context in parallel
        - Phase 2: Content extraction (key_takeaways or positions, format-dependent)
        - Phase 3: Summary
        - Phase 4: All remaining steps run in parallel (quotes, mermaid,
                   tags, image - selection depends on format)

        Returns:
            Compiled LangGraph StateGraph ready to invoke
        """
        logger.info("building_talk_workflow_graph")

        builder = StateGraph(Annotated[dict, merge_dicts])

        # --- Nodes ---
        builder.add_node("transcription", create_step_node("transcription"))
        builder.add_node("slide_markdown", create_step_node("slide_markdown"))
        builder.add_node("key_takeaways", create_step_node("key_takeaways"))
        builder.add_node("positions", create_step_node("positions"))
        builder.add_node("summary", create_step_node("summary"))
        builder.add_node("quotes", create_step_node("quotes"))
        builder.add_node("qna", create_step_node("qna"))
        builder.add_node("glossary", create_step_node("glossary"))
        builder.add_node("mermaid", create_step_node("mermaid"))
        builder.add_node("tags", create_step_node("tags"))
        builder.add_node("image", create_step_node("image"))
        builder.add_node("sondercluster", create_step_node("sondercluster"))

        builder.add_node("_load_existing_transcription", self._load_existing_transcription_node)
        builder.add_node("_init_session_context", self._init_session_context_node)
        builder.add_node("_transcription_ready", self._transcription_ready_node)
        builder.add_node("_sources_ready", self._sources_ready_node)

        # --- Edges ---

        # Phase 1: fan out to independent source/context preparation
        builder.add_conditional_edges(START, self._route_from_start)
        builder.add_edge(START, "slide_markdown")
        builder.add_edge(START, "_init_session_context")
        builder.add_edge("transcription", "_transcription_ready")
        builder.add_edge("_load_existing_transcription", "_transcription_ready")

        # Phase 2: synchronize prerequisites before format-aware routing
        builder.add_edge("_transcription_ready", "_sources_ready")
        builder.add_edge("slide_markdown", "_sources_ready")
        builder.add_edge("_init_session_context", "_sources_ready")

        # Phase 3: single content-extraction step (format-dependent)
        builder.add_conditional_edges("_sources_ready", self._route_after_init)
        builder.add_edge("key_takeaways", "summary")
        builder.add_edge("positions", "summary")

        # Phase 4: summary → parallel end steps
        builder.add_conditional_edges("summary", self._route_after_summary)

        # Phase 5: all end steps terminate the workflow
        builder.add_edge("quotes", END)
        builder.add_edge("qna", END)
        builder.add_edge("glossary", END)
        builder.add_edge("mermaid", END)
        builder.add_edge("tags", END)
        builder.add_edge("image", END)
        builder.add_edge("sondercluster", END)

        graph = builder.compile()

        logger.info(
            "talk_workflow_graph_built_successfully",
            workflow_type=self.workflow_type,
        )

        return graph
