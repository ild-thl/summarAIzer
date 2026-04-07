"""Tags step - generates topic tags for session categorization."""

import json
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.database.models import Session as SessionModel, GeneratedContent
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class TagsStep(PromptTemplate):
    """
    Generates topic tags for session categorization with configurable limits.

    Independent step (no dependencies) - can run in parallel with other steps
    Input: Session metadata + transcription (optional)
    Output: JSON array of tag strings (enforced within configured limits)

    Also updates session.tags with generated tags for easy querying.
    """

    # Configuration with defaults - can be overridden
    min_tags: int = 2
    max_tags: int = 5

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "tags"

    @property
    def dependencies(self) -> list[str]:
        """No dependencies - can run in parallel."""
        return []

    def get_model_config(self) -> ChatModelConfig:
        """Tags are shorter outputs - use configured model settings."""
        return ChatModelConfig(
            model="qwen3-30b-a3b-instruct-2507",
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
                content=f"""Du bist Expert:in für Kategorisierung technischer Inhalte mit relevanten Tags.

Deine Aufgabe ist es, {self.min_tags}-{self.max_tags} Tags für eine Veranstaltung zu generieren. Tags sollten:
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
        """Process LLM response into tags output, enforcing tag limits."""
        tags_json = response.content if hasattr(response, "content") else str(response)

        # Parse JSON response
        try:
            tags = json.loads(tags_json)
            if not isinstance(tags, list):
                tags = [tags_json]
        except json.JSONDecodeError:
            tags = [tags_json]

        # Enforce tag limits
        if len(tags) > self.max_tags:
            logger.warning(
                "tag_limit_exceeded",
                received_count=len(tags),
                max_tags=self.max_tags,
            )
            tags = tags[: self.max_tags]

        if len(tags) < self.min_tags:
            logger.warning(
                "tag_count_below_minimum",
                received_count=len(tags),
                min_tags=self.min_tags,
            )

        return {
            "content": json.dumps(tags),
            "content_type": "json_array",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_tags",
                "count": len(tags),
                "min_tags": self.min_tags,
                "max_tags": self.max_tags,
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

        Preserves manually-added tags while replacing previously-generated tags.
        - Gets the old generated tags from the previous GeneratedContent record
        - Removes old generated tags from current session.tags
        - Adds new generated tags
        - Preserves any tags that were manually added during session creation

        Args:
            db: SQLAlchemy database session
            session_id: Session ID
            execution_id: WorkflowExecution ID for tracking
            identifier: Step identifier ('tags')
            content: Dict with generated tags content
        """
        # Call parent to save to GeneratedContent table
        super()._save_to_db(db, session_id, execution_id, identifier, content)

        # Now update session.tags intelligently: preserve manual tags, replace generated ones
        try:
            # Parse new generated tags from content
            tags_json_str = content.get("content", "[]")
            new_tags = json.loads(tags_json_str) if isinstance(tags_json_str, str) else []

            if not new_tags:
                logger.info(
                    "no_tags_generated",
                    session_id=session_id,
                    execution_id=execution_id,
                )
                return

            # Fetch the session
            db_session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not db_session:
                logger.warning(
                    "session_not_found_for_tags_update",
                    session_id=session_id,
                    execution_id=execution_id,
                )
                return

            # Get current tags
            current_tags = db_session.tags or []

            # Get previously generated tags (from the second-to-last GeneratedContent record)
            # We exclude the one we just created above (identified by execution_id)
            old_generated_content = (
                db.query(GeneratedContent)
                .filter(
                    GeneratedContent.session_id == session_id,
                    GeneratedContent.identifier == "tags",
                    GeneratedContent.workflow_execution_id != execution_id,
                )
                .order_by(GeneratedContent.created_at.desc())
                .first()
            )

            # Parse old generated tags if they exist
            old_tags = []
            if old_generated_content:
                old_content = old_generated_content.content
                try:
                    old_tags = json.loads(old_content) if isinstance(old_content, str) else []
                except json.JSONDecodeError:
                    old_tags = []

            # Remove old generated tags from current tags (preserve manual tags)
            # Manual tags are those not in old_tags
            manual_tags = [tag for tag in current_tags if tag not in old_tags]

            # Combine manual tags + new generated tags
            combined_tags = list(
                dict.fromkeys(manual_tags + new_tags)
            )  # Remove duplicates while preserving order

            # Update session with combined tags
            db_session.tags = combined_tags
            db.add(db_session)
            db.commit()

            logger.info(
                "session_tags_updated_from_workflow",
                session_id=session_id,
                execution_id=execution_id,
                manual_tags_count=len(manual_tags),
                generated_tags_count=len(new_tags),
                total_tags_count=len(combined_tags),
                manual_tags=manual_tags,
                new_generated_tags=new_tags,
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
