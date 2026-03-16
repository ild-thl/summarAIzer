"""Tags step - generates topic tags for session categorization."""

import json
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class TagsStep(PromptTemplate):
    """
    Generates 10-15 topic tags for session categorization.

    Independent step (no dependencies) - can run in parallel with other steps
    Input: Session metadata + summary
    Output: JSON array of tag strings
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "tags"

    @property
    def dependencies(self) -> list[str]:
        """No dependencies - can run in parallel."""
        return []

    def get_model_config(self) -> ChatModelConfig:
        """Tags are shorter outputs - use faster model with smaller max_tokens."""
        return ChatModelConfig(
            model="meta-llama-3.1-8b-instruct",
            temperature=0.5,  # Lower for consistent tagging
            max_tokens=500,  # Tags are brief
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate tags messages with context injection."""
        categories = ", ".join(session.categories) if session.categories else "General"
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"

        return [
            SystemMessage(
                content="""Du bist Expert:in für Kategorisierung technischer Inhalte mit relevanten Tags.

Deine Aufgabe ist es, 10-15 Tags für eine Veranstaltung zu generieren. Tags sollten:
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
Kategorien: {categories}

Zusammenfassung:
{context.get('summary', '')}

Generiere nun relevante Tags für diese Veranstaltung:"""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into tags output."""
        tags_json = response.content if hasattr(response, "content") else str(response)

        # Parse JSON response
        try:
            tags = json.loads(tags_json)
            if not isinstance(tags, list):
                tags = [tags_json]
        except json.JSONDecodeError:
            tags = [tags_json]

        return {
            "content": json.dumps(tags),
            "content_type": "json_array",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_tags",
                "count": len(tags),
            },
        }


# Auto-register this step when imported
_tags_step = TagsStep()
StepRegistry.register(_tags_step)
