"""Mermaid diagram step - creates structured Mermaid mindmaps from event content."""

import re
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()


def _extract_mermaid_code(response: Any) -> str:
    """Extract Mermaid code from a raw LLM response, stripping Markdown code fences."""
    raw = response.content if hasattr(response, "content") else str(response)

    if "```mermaid" in raw:
        match = re.search(r"```mermaid\n(.*?)\n```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
    elif "```" in raw:
        match = re.search(r"```\n(.*?)\n```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()

    return raw.strip()


def _validate_mermaid(code: str) -> tuple[bool, str]:
    """Validate Mermaid mindmap syntax structurally.

    Checks:
    - First non-empty line is exactly 'mindmap'
    - At least 2 non-empty content lines in total
    - No embedded code fence markers (would break rendering)

    Returns (is_valid, reason_if_invalid).
    """
    lines = [line for line in code.splitlines() if line.strip()]

    if not lines:
        return False, "empty code"

    if lines[0].strip().lower() != "mindmap":
        return False, f"first line is not 'mindmap': {lines[0]!r}"

    if len(lines) < 2:
        return False, "fewer than 2 content lines"

    for line in lines:
        if line.strip().startswith("```"):
            return False, "embedded code fence detected"

    return True, ""


class MermaidStep(LLMStep):
    """
    Creates a Mermaid mindmap from the session summary.

    Used exclusively for Input-format talks (longer presentations with multiple themes).
    Not suitable for Lightning Talks, Workshops, Labs or Discussions.

    Input: Session metadata + summary
    Output: Valid Mermaid mindmap syntax
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "mermaid"

    @property
    def context_requirements(self) -> list[str]:
        """Requires 'summary' key in context for diagram generation."""
        return ["summary"]

    def get_model_config(self) -> ChatModelConfig:
        """Mermaid generation needs structured output - low temperature for consistency."""
        return ChatModelConfig(
            model="devstral-2-123b-instruct-2512",
            temperature=0.2,
            max_tokens=1500,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate mermaid messages with context injection."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unbekannt"

        return [
            SystemMessage(
                content="""Du erstellst strukturierte Mindmap-Diagramme für Bildungsveranstaltungen im Mermaid-Format.

Verwende folgende Regeln:
- Hauptthema als zentralen Knoten (Wurzel)
- Maximal 5-6 Hauptäste (Kernthemen aus der Zusammenfassung)
- Jeder Hauptast hat 2-3 Unterknoten mit konkreten Inhalten
- Beschriftungen kurz und prägnant (maximal 8 Wörter pro Knoten)
- Nur Inhalte aufnehmen, die in der Zusammenfassung tatsächlich vorkommen – keine Erfindungen

Gib NUR validen Mermaid-Code zurück, ohne weitere Erklärungen, in folgendem Format:

```mermaid
mindmap
  root)Hauptthema(
    Kernthema 1
      Unterpunkt 1.1
      Unterpunkt 1.2
    Kernthema 2
      Unterpunkt 2.1
      Unterpunkt 2.2
```"""
            ),
            HumanMessage(
                content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}

Zusammenfassung:
{context.get('summary', '')}

Erstelle nun eine Mermaid-Mindmap für diese Veranstaltung."""
            ),
        ]

    async def _invoke_and_process(
        self, session: SessionModel, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Invoke LLM, validate Mermaid syntax, and retry once with a correction prompt if invalid.

        Flow:
        1. Generate initial Mermaid mindmap code.
        2. Validate: first line must be 'mindmap', must have ≥2 content lines, no embedded fences.
        3. If invalid: send one correction request to the LLM and validate again.
        4. If still invalid after retry: return empty content with validation_failed=True in meta.
        """
        messages = self.get_messages(session, context)
        response = await self.get_model().ainvoke(messages)
        mermaid_code = _extract_mermaid_code(response)

        is_valid, reason = _validate_mermaid(mermaid_code)

        if not is_valid:
            logger.warning(
                "mermaid_step_invalid_syntax_retrying",
                session_id=session.id,
                reason=reason,
            )

            correction_messages = [
                *messages,
                response,
                HumanMessage(
                    content=(
                        f"Der generierte Mermaid-Code ist ungültig ({reason}). "
                        "Korrigiere ihn so, dass er mit 'mindmap' beginnt, mindestens zwei Knoten enthält "
                        "und keine eingebetteten Code-Blöcke enthält. "
                        "Gib ausschließlich den korrigierten Mermaid-Code zurück."
                    )
                ),
            ]
            retry_response = await self.get_model().ainvoke(correction_messages)
            mermaid_code = _extract_mermaid_code(retry_response)
            is_valid, reason = _validate_mermaid(mermaid_code)

            if not is_valid:
                logger.error(
                    "mermaid_step_validation_failed_after_retry",
                    session_id=session.id,
                    reason=reason,
                )
                return {
                    "content": "",
                    "content_type": "mermaid",
                    "meta_info": {
                        "model": self.get_model_config().model,
                        "type": "generated_mermaid_diagram",
                        "diagram_type": "mindmap",
                        "validation_failed": True,
                        "validation_reason": reason,
                    },
                }

        return {
            "content": mermaid_code,
            "content_type": "mermaid",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_mermaid_diagram",
                "diagram_type": "mindmap",
            },
        }

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response to mermaid output (fallback path, not used by default)."""
        mermaid_code = _extract_mermaid_code(response)

        return {
            "content": mermaid_code,
            "content_type": "mermaid",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_mermaid_diagram",
                "diagram_type": "mindmap",
            },
        }


# Auto-register this step when imported
_mermaid_step = MermaidStep()
StepRegistry.register(_mermaid_step)
