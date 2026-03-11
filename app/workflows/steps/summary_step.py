"""Summary step - generates markdown summary of session."""

from typing import Dict, Any, List
from sqlalchemy.orm import Session
import structlog
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage

from app.database.models import Session as SessionModel
from app.workflows.steps.prompt_template import PromptTemplate
from app.workflows.chat_models import ChatModelConfig

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
    def dependencies(self) -> List[str]:
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

    def get_messages(
        self, session: SessionModel, context: Dict[str, Any]
    ) -> List[BaseMessage]:
        """Generate summary messages with context injection."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"
        duration = session.duration or 0
        categories = ", ".join(session.categories) if session.categories else "General"
        
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
Kategorien: {categories}

Transkript:
{context.get('transcription', '')}

Erstelle nun eine strukturierte Markdown-Zusammenfassung der Veranstaltung."""
            ),
        ]

    def process_response(self, response: Any) -> Dict[str, Any]:
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
from app.workflows.execution_context import StepRegistry
_summary_step = SummaryStep()
StepRegistry.register(_summary_step)
