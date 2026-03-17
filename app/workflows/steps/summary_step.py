"""Summary step - generates markdown summary of session."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class SummaryStep(PromptTemplate):
    """
    Generates a comprehensive markdown summary of a session.

    Input: Session metadata + transcription
    Output: Markdown formatted summary with:
        - Übersicht (Overview)
        - Kernaussagen (Key Takeaways)
        - Lernziele (Learning Objectives)
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "summary"

    @property
    def dependencies(self) -> list[str]:
        """No dependencies - can run first."""
        return []

    def get_model_config(self) -> ChatModelConfig:
        """Summary needs good context - use model with larger max_tokens."""
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.7,
            max_tokens=3000,  # Larger for comprehensive summaries
            top_p=0.95,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate summary messages with context injection."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"
        duration = session.duration or 0
        tags = ", ".join(session.tags) if session.tags else "General"

        return [
            SystemMessage(
                content="""Du bist ein Assistent, der Veranstaltungen zusammenfasst. Du erstellst Dokumentationen aus Transkripten mit folgenden Eigenschaften:

- Klare, didaktische Sprache auf Deutsch
- Keine Halluzinationen: Nur Fakten aus dem Transkript verwenden
- Strukturierte Gliederung mit max. zwei Überschriftsebenen
- Zitate kursiv in Anführungszeichen
- Fokus auf Kernaussagen und Handlungsempfehlungen

Deine Zusammenfassung enthält:
1. **Übersicht** - 2-3 Absätze zum Hauptthema
2. **Kernaussagen** - Mit direkten Zitaten aus dem Transkript
3. **Lernziele & Kompetenzen** - Was Teilnehmende lernen
4. **Handlungsempfehlungen** - Call-to-Actions und nächste Schritte
5. **Metadaten** - Zielgruppe, Voraussetzungen

Format: Markdown, bereit zum Kopieren."""
            ),
            HumanMessage(
                content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}
Dauer: {duration} Minuten
Tags: {tags}

Transkript:
{context.get('transcription', '')}

Erstelle nun eine strukturierte Markdown-Zusammenfassung der Veranstaltung."""
            ),
        ]

    def validate_scheduling_requirements(self, session_id: int, db: Session) -> None:
        """
        Validate that transcription exists before scheduling summary task.

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
                "summary_scheduling_failed_no_transcription",
                session_id=session_id,
                reason="Summary step requires transcription to generate comprehensive summaries",
            )
            raise ValueError(
                f"Cannot schedule summary generation for session {session_id}: "
                "Transcription is required. "
                "Please upload transcription content before generating summary."
            )

        logger.info(
            "summary_scheduling_requirements_validated",
            session_id=session_id,
            has_transcription=True,
        )

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into summary output."""
        summary = response.content if hasattr(response, "content") else str(response)

        return {
            "content": summary,
            "content_type": "markdown",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_summary",
            },
        }


# Auto-register this step when imported
_summary_step = SummaryStep()
StepRegistry.register(_summary_step)
