"""Summary step - generates markdown summary of session."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.database.models import SessionFormat
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


_FORMAT_SECTIONS: dict[str, tuple[str, list[str]]] = {
    "talk": (
        "Vortrag",
        ["Übersicht", "Kernaussagen", "Handlungsempfehlungen"],
    ),
    "discussion": (
        "Diskussion",
        [
            "Übersicht",
            "Positionen & Perspektiven",
            "Zentrale Streitpunkte",
            "Ergebnisse & offene Fragen",
        ],
    ),
    "workshop": (
        "Workshop/Training",
        [
            "Übersicht",
            "Vermittelte Methoden & Tools",
            "Übungen & Aktivitäten",
            "Kernaussagen",
            "Handlungsempfehlungen",
        ],
    ),
}


def _get_format_config(session_format) -> tuple[str, list[str]]:
    """Map session format to format label and section list."""
    if session_format in (SessionFormat.DISCUSSION,):
        return _FORMAT_SECTIONS["discussion"]
    if session_format in (SessionFormat.WORKSHOP, SessionFormat.TRAINING):
        return _FORMAT_SECTIONS["workshop"]
    return _FORMAT_SECTIONS["talk"]  # INPUT, LIGHTNING_TALK, None, unknown


class SummaryStep(PromptTemplate):
    """
    Generates a comprehensive markdown summary of a session.

    Independent step that optionally uses key takeaways if available in context
    for more complete coverage, but can also generate standalone summaries.
    Supports format-aware prompts (talk, discussion, workshop).

    Input: Session metadata + transcription (+ optional key_takeaways)
    Output: Markdown formatted summary with format-specific sections
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "summary"

    @property
    def context_requirements(self) -> list[str]:
        """Requires transcription to generate summary.

        Optionally uses 'key_takeaways' if available in context for more complete coverage.
        """
        return ["transcription"]

    def get_model_config(self) -> ChatModelConfig:
        """Summary needs good context - use model with larger max_tokens."""
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.7,
            max_tokens=3000,  # Larger for comprehensive summaries
            top_p=0.95,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate format-aware summary messages with optional key takeaways context."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"
        duration = session.duration or 0
        tags = ", ".join(session.tags) if session.tags else "General"

        format_label, sections = _get_format_config(session.session_format)
        sections_text = "\n".join(f"{i+1}. **{s}**" for i, s in enumerate(sections))
        key_takeaways_block = context.get("key_takeaways", "")

        # Build system message
        sys_base = f"""Du bist ein Assistent, der {format_label}-Veranstaltungen zusammenfasst. Du erstellst Dokumentationen aus Transkripten mit folgenden Eigenschaften:

- Klare, didaktische Sprache auf Deutsch
- Keine Halluzinationen: Nur Fakten aus dem Transkript verwenden
- Strukturierte Gliederung mit max. zwei Überschriftsebenen
- Zitate kursiv in Anführungszeichen
- Fokus auf Kernaussagen und Handlungsempfehlungen

Deine Zusammenfassung enthält diese Abschnitte:
{sections_text}

Format: Markdown, bereit zum Kopieren."""

        if key_takeaways_block:
            sys_message = (
                sys_base + "\n\nDecke alle vorab extrahierten Key Takeaways vollständig ab."
            )
        else:
            sys_message = sys_base

        # Build human message
        human_base = f"""Veranstaltung: {session.title}
Referent:innen: {speakers}
Dauer: {duration} Minuten
Tags: {tags}

Transkript:
{context.get('transcription', '')}

Erstelle nun eine strukturierte Markdown-Zusammenfassung der Veranstaltung."""

        if key_takeaways_block:
            human_message = f"""Veranstaltung: {session.title}
Referent:innen: {speakers}
Dauer: {duration} Minuten
Tags: {tags}

Vorab extrahierte Key Takeaways:
{key_takeaways_block}

Transkript:
{context.get('transcription', '')}

Erstelle nun eine strukturierte Markdown-Zusammenfassung der Veranstaltung."""
        else:
            human_message = human_base

        return [
            SystemMessage(content=sys_message),
            HumanMessage(content=human_message),
        ]

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
