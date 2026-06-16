"""Key takeaways step - extracts core points from talk, workshop and lab sessions."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.database.models import SessionFormat
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()

# Lightning Talk sessions get a compact extraction
_LIGHTNING_TALK_COUNT = "3-4"
_DEFAULT_COUNT = "5-8"


class KeyTakeawaysStep(LLMStep):
    """
    Extracts core points (Kernpunkte) from talk, workshop and lab sessions.

    Not used for discussion sessions (use PositionsStep instead).
    Lightning Talk sessions get a reduced count (3-4) for a compact output.

    Input: Session metadata + transcription
    Output: Markdown list of core points used as input for the summary step
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "key_takeaways"

    @property
    def context_requirements(self) -> list[str]:
        """Requires transcription to extract key takeaways."""
        return ["transcription"]

    def get_model_config(self) -> ChatModelConfig:
        """Key takeaways need nuanced understanding - use well-rounded model."""
        return ChatModelConfig(
            model="mistral-large-3-675b-instruct-2512",
            temperature=0.1,
            max_tokens=1500,
            top_p=0.92,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate key takeaways messages, compact variant for Lightning Talks."""
        is_lightning = session.session_format == SessionFormat.LIGHTNING_TALK
        count = _LIGHTNING_TALK_COUNT if is_lightning else _DEFAULT_COUNT
        format_label = session.session_format.value if session.session_format else "Vortrag"

        return [
            SystemMessage(
                content=f"""Du bist Expert:in für die Extraktion von Kernaussagen aus Bildungsveranstaltungen.

Deine Aufgabe ist es, {count} spezifische, inhaltlich eigenständige Kernpunkte zu extrahieren. Jeder Kernpunkt sollte:
- Klar und prägnant formuliert sein (1-2 Sätze)
- Einen eigenständigen Informationswert haben
- Direkt aus dem Inhalt der Veranstaltung stammen (keine Erfindungen)
- Für Teilnehmende relevant und auf die Problemstellung aus der Session-Beschreibung bezogen sein

Gib AUSSCHLIESSLICH eine Markdown-Liste zurück, ohne weitere Einleitungen oder Erklärungen.
Beispiel:
 - Kernpunkt 1
 - Kernpunkt 2
 - ..."""
            ),
            HumanMessage(
                content=f"""Veranstaltungstitel: {session.title}
Format: {format_label}
Referent:innen: {", ".join(session.speakers) if session.speakers else "Unbekannt"}

Session-Beschreibung:
{session.description or 'Keine Beschreibung verfügbar.'}

Transkript:
{context.get('transcription', '')}

Extrahiere nun {count} Kernpunkte dieser Veranstaltung."""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into key takeaways output."""
        takeaways = response.content if hasattr(response, "content") else str(response)
        # Extract markdown from code fence wrappers if present
        takeaways = self._extract_markdown_from_code_fences(takeaways)

        return {
            "content": takeaways,
            "content_type": "markdown",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_key_takeaways",
            },
        }


# Auto-register this step when imported
_key_takeaways_step = KeyTakeawaysStep()
StepRegistry.register(_key_takeaways_step)
