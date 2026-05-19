"""Summary step - generates format-specific markdown summaries of session content."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.database.models import SessionFormat
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()


def _build_talk_messages(
    session: SessionModel,
    context: dict[str, Any],
    is_lightning: bool,
) -> list[BaseMessage]:
    """Build summary messages for talk/impuls format (Input, Lightning Talk)."""
    speakers = ", ".join(session.speakers) if session.speakers else "Unbekannt"
    duration = session.duration or 0
    key_takeaways = context.get("key_takeaways", "")
    description = session.description or ""

    if is_lightning:
        sections_text = (
            "1. **Kontext & Einordnung** – Worum geht es? Einordnung in den größeren Zusammenhang, 2-3 Sätze\n"
            "2. **Kernaussage** – Die zentrale inhaltliche Botschaft dieses Lightning Talks\n"
            "3. **Argumente & Belege** – Stützende Belege, Zahlen oder Beispiele (weglassen wenn nicht ableitbar)\n"
            "4. **Handlungsempfehlung** – Konkrete Empfehlung oder Botschaft (weglassen wenn nicht ableitbar)"
        )
        length_instruction = "\nHalte die Zusammenfassung kompakt (max. ~250 Wörter)."
        task_instruction = (
            "Erstelle nun eine kompakte Markdown-Zusammenfassung dieses Lightning Talks."
        )
        sahnehaeubchen_note = ""
    else:
        sections_text = (
            "1. **Kontext & Einordnung** – Worum geht es? Kontext und Einordnung in den Themenzusammenhang, 2-4 Sätze\n"
            "2. **Kernaussagen** – Die wichtigsten inhaltlichen Punkte (max 2-6); adressiert die Problemstellung aus der Session-Beschreibung\n"
            "3. **Argumente & Belege** – Fakten, Zahlen und Beispiele; nur falls vorhanden\n"
            "4. **Handlungsempfehlung** – Konkrete Empfehlung oder Botschaft; nur falls ableitbar alternativ kuzes Fazit"
        )
        length_instruction = ""
        task_instruction = (
            "Erstelle nun eine strukturierte Markdown-Zusammenfassung dieses Vortrags."
        )
        sahnehaeubchen_note = "\n\nAbschnitte 4 und 5 weglassen statt mit Platzhaltern füllen."

    coverage_instruction = (
        "\n\nDecke in der Zusammenfassung alle vorab extrahierten Kernpunkte vollständig ab."
        if key_takeaways
        else ""
    )

    system_message = f"""Du bist ein Assistent, der Vorträge und Impuls-Präsentationen dokumentiert. Du erstellst präzise, gut lesbare Zusammenfassungen mit folgenden Eigenschaften:

- Klare, sachliche Sprache auf Deutsch
- Keine Halluzinationen: Ausschließlich Inhalte aus Transkription und Kernpunkten verwenden
- Strukturierte Gliederung mit maximal zwei Überschriftsebenen
- Keine wörtlichen Zitate aus der Transkription
- Fachbegriffe und Tool-Namen exakt aus dem Original übernehmen

Deine Zusammenfassung enthält diese Abschnitte:
{sections_text}{length_instruction}

Format: Markdown.{sahnehaeubchen_note}{coverage_instruction}"""

    human_parts = [
        f"Veranstaltung: {session.title}",
        f"Referent:innen: {speakers}",
        f"Dauer: {duration} Minuten",
    ]

    if description:
        human_parts.append(f"\nSession-Beschreibung und Problemstellung:\n{description}")

    if key_takeaways:
        human_parts.append(f"\nVorab extrahierte Kernpunkte:\n{key_takeaways}")

    human_parts.append(f"\nTranskript:\n{context.get('transcription', '')}")
    human_parts.append(f"\n{task_instruction}")

    return [SystemMessage(content=system_message), HumanMessage(content="\n".join(human_parts))]


def _build_workshop_messages(
    session: SessionModel,
    context: dict[str, Any],
) -> list[BaseMessage]:
    """Build summary messages for workshop/training/lab format."""
    speakers = ", ".join(session.speakers) if session.speakers else "Unbekannt"
    duration = session.duration or 0
    format_label = (
        session.session_format.value.capitalize() if session.session_format else "Workshop"
    )
    key_takeaways = context.get("key_takeaways", "")
    description = session.description or ""

    coverage_instruction = (
        "\n\nDecke in der Zusammenfassung alle vorab extrahierten Kernpunkte vollständig ab."
        if key_takeaways
        else ""
    )

    system_message = f"""Du bist ein Assistent, der Workshops, Trainings und Lab-Sessions dokumentiert. Du erstellst praxisnahe, strukturierte Dokumentationen mit folgenden Eigenschaften:

- Klare, anleitende Sprache auf Deutsch
- Keine Erfindungen: Ausschließlich Inhalte aus Transkription und Kernpunkten verwenden
- Besonderer Fokus auf vermittelte Methoden, Tools und praktische Aktivitäten
- Übungen und ihre Zielsetzungen explizit benennen – nicht nur erwähnen, dass Übungen stattfanden
- Keine wörtlichen Zitate aus der Transkription

Deine Zusammenfassung enthält diese Abschnitte (Workshop):
1. **Lernziele** – 3-4 konkrete Kompetenzen, die Teilnehmende erwerben (als Liste)
2. **Methodik & Ablauf** – Ablauf und eingesetzte Methoden und Frameworks
3. **Kerninhalte** – Inhaltliche Substanz; adressiert die Problemstellung aus der Session-Beschreibung
4. **Arbeitsergebnisse** – Erkenntnisse und Outputs aus Gruppenarbeit oder Arbeitsphasen (wenn ableitbar)
5. **Handlungsempfehlung & Transfer** – Wie können die Inhalte in der Praxis angewendet werden? (wenn ableitbar)

Abschnitte 4-5 weglassen wenn kein Material vorhanden.

Format: Markdown.{coverage_instruction}"""

    human_parts = [
        f"Veranstaltung: {session.title}",
        f"Format: {format_label}",
        f"Referent:innen: {speakers}",
        f"Dauer: {duration} Minuten",
    ]

    if description:
        human_parts.append(f"\nSession-Beschreibung und Problemstellung:\n{description}")

    if key_takeaways:
        human_parts.append(f"\nVorab extrahierte Kernpunkte:\n{key_takeaways}")

    human_parts.append(f"\nTranskript:\n{context.get('transcription', '')}")
    human_parts.append(
        "\nErstelle nun eine strukturierte Markdown-Dokumentation dieser Veranstaltung."
    )

    return [SystemMessage(content=system_message), HumanMessage(content="\n".join(human_parts))]


def _build_discussion_messages(
    session: SessionModel,
    context: dict[str, Any],
) -> list[BaseMessage]:
    """Build summary messages for discussion/panel format."""
    speakers = ", ".join(session.speakers) if session.speakers else "Unbekannte Diskutierende"
    duration = session.duration or 0
    positions = context.get("positions", "")
    description = session.description or ""

    system_message = """Du bist ein Assistent, der Diskussionen und Panel-Gespräche objektiv dokumentiert. Du erstellst ausgewogene, neutrale Berichte mit folgenden Eigenschaften:

- Neutrale, berichtende Sprache auf Deutsch – keine eigene Position
- Verschiedene Perspektiven fair und vollständig darstellen
- Sprecher:innen namentlich zuordnen, soweit aus dem Transkript erkennbar
- Mehrheitsmeinungen und Minderheitsmeinungen klar unterscheiden
- Offene Fragen und Uneinigkeiten explizit als solche kennzeichnen
- Keine wörtlichen Zitate aus der Transkription

Deine Zusammenfassung enthält diese Abschnitte:
1. **Kontext & Einordnung** – Ausgangsfrage, These oder Rahmensetzung der Diskussion
2. **Diskussionslinien** – Zentrale Fragestellungen und Positionen; adressiert die Problemstellung
3. **Perspektiven der Beteiligten** – Position A, Position B etc. mit namentlicher Zuordnung
4. **Argumente & Belege** – Stützende Fakten und Beispiele je Perspektive
5. **Offene Fragen** – Was blieb ungeklärt? (weglassen wenn kein Material)
6. **Ergebnis & Handlungsempfehlung** – Was wurde (vorläufig) geklärt? Was nehmen Teilnehmende mit? (weglassen wenn kein Material)

Format: Markdown."""

    human_parts = [
        f"Veranstaltung: {session.title}",
        f"Diskutierende: {speakers}",
        f"Dauer: {duration} Minuten",
    ]

    if description:
        human_parts.append(f"\nSession-Beschreibung und Problemstellung:\n{description}")

    if positions:
        human_parts.append(f"\nVorab extrahierte Positionen & Zitate:\n{positions}")

    human_parts.append(f"\nTranskript:\n{context.get('transcription', '')}")
    human_parts.append("\nErstelle nun eine ausgewogene Markdown-Dokumentation dieser Diskussion.")

    return [SystemMessage(content=system_message), HumanMessage(content="\n".join(human_parts))]


class SummaryStep(LLMStep):
    """
    Generates a format-specific markdown summary of a session.

    Routes to one of three prompt variants based on session format:
    - Talk/Impuls (Input, Lightning Talk): structured overview with key statements
    - Workshop/Training/Lab: practice-oriented with methods, activities, objectives
    - Discussion/Panel: neutral multi-perspective report

    Context used:
    - transcription (required)
    - key_takeaways (optional, used for talk/workshop paths)
    - positions (optional, used for discussion path)

    Output: Markdown formatted summary with format-specific sections
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "summary"

    @property
    def context_requirements(self) -> list[str]:
        """Requires transcription. Optionally uses key_takeaways or positions."""
        return ["transcription"]

    def get_model_config(self) -> ChatModelConfig:
        """Summary needs good context comprehension and longer output."""
        return ChatModelConfig(
            model="mistral-large-3-675b-instruct-2512",
            temperature=0.7,
            max_tokens=5000,
            top_p=0.95,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate summary messages based on format."""
        is_lightning = session.session_format == SessionFormat.LIGHTNING_TALK
        is_discussion = session.session_format in {SessionFormat.DISCUSSION}
        is_workshop = session.session_format in {
            SessionFormat.WORKSHOP,
            SessionFormat.TRAINING,
            SessionFormat.LAB,
        }

        if is_discussion:
            return _build_discussion_messages(session, context)
        elif is_workshop:
            return _build_workshop_messages(session, context)
        else:
            return _build_talk_messages(session, context, is_lightning)

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into summary output."""
        summary = response.content if hasattr(response, "content") else str(response)
        # Extract markdown from code fence wrappers if present
        summary = self._extract_markdown_from_code_fences(summary)

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
