"""Positions step - extracts speaker positions and quotes from discussion sessions."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()


class PositionsStep(LLMStep):
    """
    Extracts speaker positions and illustrative quotes from discussion/panel sessions.

    Used exclusively for discussion-format sessions. Replaces the key_takeaways step
    in the discussion workflow path. Structures the transcript by person and standpoint
    to enable fair, attributable documentation of different perspectives.

    Input: Session metadata + transcription
    Output: Structured markdown list of positions per speaker with direct quotes
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "positions"

    @property
    def context_requirements(self) -> list[str]:
        """Requires transcription to extract positions."""
        return ["transcription"]

    def get_model_config(self) -> ChatModelConfig:
        """Positions extraction needs careful attribution - use balanced model."""
        return ChatModelConfig(
            model="gemma-4-31b-it",
            temperature=0.1,
            max_tokens=2000,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate positions extraction messages for discussion sessions."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unbekannte Diskutierende"
        duration = session.duration or 0

        return [
            SystemMessage(
                content="""Du analysierst Diskussionen und Panelgespräche und extrahierst die Positionen der Teilnehmenden.

Für jede erkennbare Sprechperson erstelle einen Eintrag mit:
- Name / Bezeichnung der Person (falls erkennbar, sonst "Person A", "Person B", ...)
- Ihre Kernposition zum Diskussionsthema (1-3 Sätze)
- 1-2 direkte Zitate, die ihre Position illustrieren

Wichtige Hinweise:
- Nur Positionen und Zitate aufnehmen, die tatsächlich im Transkript belegt sind
- Keine Positionen erfinden oder aus dem Kontext schlussfolgern
- Bei unklarer Zuordnung lieber keine Zuordnung als falsche Zuordnung
- Zitate wortgetreu aus dem Transkript übernehmen

Gib das Ergebnis als strukturierte Markdown-Liste zurück."""
            ),
            HumanMessage(
                content=f"""Diskussionsthema: {session.title}
Diskutierende: {speakers}
Dauer: {duration} Minuten

Session-Beschreibung:
{session.description or 'Keine Beschreibung verfügbar.'}

Transkript:
{context.get('transcription', '')}

Extrahiere nun die Positionen und Kernaussagen der einzelnen Diskutierenden."""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into positions output."""
        positions = response.content if hasattr(response, "content") else str(response)
        # Extract markdown from code fence wrappers if present
        positions = self._extract_markdown_from_code_fences(positions)

        return {
            "content": positions,
            "content_type": "markdown",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_positions",
            },
        }


# Auto-register this step when imported
_positions_step = PositionsStep()
StepRegistry.register(_positions_step)
