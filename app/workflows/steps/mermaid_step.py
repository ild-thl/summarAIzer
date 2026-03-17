"""Mermaid diagram step - creates structured Mermaid mindmaps from event content."""

import re
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class MermaidStep(PromptTemplate):
    """
    Creates structured Mermaid mindmaps from event content.

    Visualizes key points, quotes and logical connections.
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "mermaid"

    @property
    def dependencies(self) -> list[str]:
        """Depends on summary for context."""
        return ["summary"]

    def get_model_config(self) -> ChatModelConfig:
        """Mermaid generation needs structured output."""
        return ChatModelConfig(
            model="codestral-22b",  # Good for code/structure
            temperature=0.2,  # Low for consistency
            max_tokens=2000,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate mermaid messages with context injection."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"

        return [
            SystemMessage(
                content="""Du erstellst strukturierte Mermaid-Mindmaps für Bildungsveranstaltungen. Verwende die Mermaid-Mindmap-Syntax mit folgenden Regeln:

- Hauptthema als Wurzel
- Max. 5-6 Hauptäste (Kernpunkte)
- Jeder Hauptast hat 2-3 Unterpunkte
- Relevante Zitate als Unterpunkte: "Zitat: '...'"
- Separate Äste für: Technologien/Tools, Herausforderungen
- Klare, prägnante Beschriftungen (max. 10 Wörter)

Gib NUR valides Mermaid-Code zurück, im folgenden Format:

```mermaid
mindmap
  root)Hauptthema(
    Kernpunkt 1
      Unterpunkt 1.1
      "Zitat: 'direkter Text'"
    Kernpunkt 2
      Unterpunkt 2.1
      Unterpunkt 2.2
    Technologien/Tools
      Tool 1
      Methode 1
    Herausforderungen
      Challenge 1
      Lösungsansatz 1
```"""
            ),
            HumanMessage(
                content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}

Zusammenfassung:
{context.get('summary', '')}

Transkript (für Zitate):
{context.get('transcription', '')}

Erstelle nun eine Mermaid-Mindmap für diese Veranstaltung."""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response to mermaid output."""
        mermaid_code = response.content if hasattr(response, "content") else str(response)

        # Extract mermaid code from markdown code block if needed
        if "```mermaid" in mermaid_code:
            match = re.search(r"```mermaid\n(.*?)\n```", mermaid_code, re.DOTALL)
            if match:
                mermaid_code = match.group(1)
        elif "```" in mermaid_code:
            match = re.search(r"```\n(.*?)\n```", mermaid_code, re.DOTALL)
            if match:
                mermaid_code = match.group(1)

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
