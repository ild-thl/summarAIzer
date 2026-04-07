"""Key takeaways step - extracts actionable insights from session."""

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


class KeyTakeawaysStep(PromptTemplate):
    """
    Extracts 6-8 actionable key takeaways from the session.

    Depends on: SummaryStep (uses summary for context)
    Input: Session metadata + transcription + summary
    Output: JSON array of actionable takeaway strings
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "key_takeaways"

    @property
    def dependencies(self) -> list[str]:
        """No dependencies - runs first to identify key points before summary."""
        return []

    def validate_scheduling_requirements(self, session_id: int, db: Session) -> None:
        """
        Validate that transcription exists before scheduling key takeaways task.

        Called at workflow scheduling time to fail fast if transcription hasn't
        been uploaded yet (rather than waiting for task execution).

        Args:
            session_id: Session ID
            db: Database session

        Raises:
            ValueError: If transcription is not available
        """
        # Import here to avoid circular imports
        from app.crud import generated_content as content_crud

        tx_content = content_crud.get_content_by_identifier(db, session_id, "transcription")
        if not tx_content:
            logger.error(
                "key_takeaways_scheduling_failed_no_transcription",
                session_id=session_id,
                reason="Key Takeaways step requires transcription to extract points",
            )
            raise ValueError(
                f"Cannot schedule key takeaways generation for session {session_id}: "
                "Transcription is required. "
                "Please upload transcription content before generating key takeaways."
            )

        logger.info(
            "key_takeaways_scheduling_requirements_validated",
            session_id=session_id,
            has_transcription=True,
        )
        """Key takeaways need nuanced understanding - use well-rounded model."""
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.6,  # Moderate for balanced extraction
            max_tokens=1500,  # Medium output for 6-8 takeaways
            top_p=0.92,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate key takeaways messages with context injection."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"

        return [
            SystemMessage(
                content="""Du bist Expert:in für die Extrahierung von Key Takeaways aus technischen Veranstaltungen.

Deine Aufgabe ist es, 6-8 spezifische, umsetzbare Key Takeaways zu extrahieren. Jeder Takeaway sollte:
- Klar und prägnant sein (1-2 Sätze)
- Umsetzbar für Teilnehmende sein
- Spezifisch zu diesem Veranstaltungsinhalt sein
- Mit direkten Zitaten aus dem Transkript belegt werden können

Gib AUSSCHLIESSLICH ein JSON-Array von Strings zurück, nichts anderes. Beispiel:
["Takeaway 1", "Takeaway 2", ...]"""
            ),
            HumanMessage(
                content=f"""Veranstaltungstitel: {session.title}
Referent:innen: {speakers}

Transkript:
{context.get('transcription', '')}

Extrahiere nun die Key Takeaways:"""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into key takeaways output."""
        takeaways_json = response.content if hasattr(response, "content") else str(response)

        # Parse JSON response
        try:
            takeaways = json.loads(takeaways_json)
            if not isinstance(takeaways, list):
                takeaways = [takeaways_json]
        except json.JSONDecodeError:
            takeaways = [takeaways_json]

        return {
            "content": json.dumps(takeaways),
            "content_type": "json_array",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_key_takeaways",
                "count": len(takeaways),
            },
        }


# Auto-register this step when imported
_key_takeaways_step = KeyTakeawaysStep()
StepRegistry.register(_key_takeaways_step)
