"""Tags step - generates topic tags for session categorization."""

import json
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class TagsStep(PromptTemplate):
    """
    Generates 10-15 topic tags for session categorization.

    Independent step (no dependencies) - can run in parallel with other steps
    Input: Session metadata + transcription (optional)
    Output: JSON array of tag strings

    Also updates session.tags with generated tags for easy querying.
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "tags"

    @property
    def dependencies(self) -> list[str]:
        """No dependencies - can run in parallel."""
        return []

    def get_model_config(self) -> ChatModelConfig:
        """Tags are shorter outputs - use faster model with smaller max_tokens."""
        return ChatModelConfig(
            model="meta-llama-3.1-8b-instruct",
            temperature=0.5,  # Lower for consistent tagging
            max_tokens=500,  # Tags are brief
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """
        Generate tags messages with context injection.

        Uses transcription if available, otherwise falls back to session.short_description.
        This allows tag generation to work independently of transcription availability.
        """
        existing_tags = ", ".join(session.tags) if session.tags else "General"
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"

        # Use transcription if available, otherwise fall back to short_description
        main_content = context.get("transcription") or session.short_description or ""

        return [
            SystemMessage(
                content="""Du bist Expert:in für Kategorisierung technischer Inhalte mit relevanten Tags.

Deine Aufgabe ist es, 10-15 Tags für eine Veranstaltung zu generieren. Tags sollten:
- Kleinbuchstaben und mit Bindestrichen versehen (z.B. "maschinelles-lernen", "webentwicklung")
- Technologien, Themen, Use Cases, Skilllevels abdecken
- Spezifisch und aussagekräftig für die Kategorisierung sein
- Keine Redundanzen aufweisen

Gib AUSSCHLIESSLICH ein JSON-Array von Strings zurück, nichts anderes. Beispiel:
["tag1", "tag2", "tag3", ...]"""
            ),
            HumanMessage(
                content=f"""Veranstaltungstitel: {session.title}
Referent:innen: {speakers}
Existierende Tags: {existing_tags}

Beschreibung/Transkript:
{main_content}

Generiere nun relevante Tags für diese Veranstaltung:"""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into tags output."""
        tags_json = response.content if hasattr(response, "content") else str(response)

        # Parse JSON response
        try:
            tags = json.loads(tags_json)
            if not isinstance(tags, list):
                tags = [tags_json]
        except json.JSONDecodeError:
            tags = [tags_json]

        return {
            "content": json.dumps(tags),
            "content_type": "json_array",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_tags",
                "count": len(tags),
            },
        }

    def _save_to_db(
        self,
        db: Session,
        session_id: int,
        execution_id: int,
        identifier: str,
        content: dict[str, Any],
    ) -> None:
        """
        Save generated tags to database and update session.tags.

        Extends parent behavior to also store tags directly on the session model
        for easier filtering and querying based on tags.

        Args:
            db: SQLAlchemy database session
            session_id: Session ID
            execution_id: WorkflowExecution ID for tracking
            identifier: Step identifier ('tags')
            content: Dict with generated tags content
        """
        # Call parent to save to GeneratedContent table
        super()._save_to_db(db, session_id, execution_id, identifier, content)

        # Now update session.tags with generated tags
        try:
            # Parse generated tags from content
            tags_json_str = content.get("content", "{}")
            tags_list = json.loads(tags_json_str) if isinstance(tags_json_str, str) else []

            if tags_list:
                # Fetch the session and update its tags
                db_session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
                if db_session:
                    # Update tags, preserving existing tags and adding generated ones
                    existing_tags = db_session.tags or []
                    # Combine and deduplicate while preserving order
                    combined_tags = list(dict.fromkeys(existing_tags + tags_list))
                    db_session.tags = combined_tags
                    db.add(db_session)
                    db.commit()
                    logger.info(
                        "session_tags_updated_from_workflow",
                        session_id=session_id,
                        execution_id=execution_id,
                        tag_count=len(combined_tags),
                    )
                else:
                    logger.warning(
                        "session_not_found_for_tags_update",
                        session_id=session_id,
                        execution_id=execution_id,
                    )

        except Exception as e:
            logger.error(
                "failed_to_update_session_tags",
                session_id=session_id,
                execution_id=execution_id,
                error=str(e),
                exc_info=True,
            )
            # Don't raise - content was already saved to generated_content table


# Auto-register this step when imported
_tags_step = TagsStep()
StepRegistry.register(_tags_step)
