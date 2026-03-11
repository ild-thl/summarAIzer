"""Key takeaways step - extracts actionable insights from session."""

import json
from typing import Dict, Any, List
import structlog
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage

from app.database.models import Session as SessionModel
from app.workflows.steps.prompt_template import PromptTemplate
from app.workflows.chat_models import ChatModelConfig

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
    def dependencies(self) -> List[str]:
        """Depends on summary for better context."""
        return ["summary"]

    def get_model_config(self) -> ChatModelConfig:
        """Key takeaways need nuanced understanding - use well-rounded model."""
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.6,  # Moderate for balanced extraction
            max_tokens=1500,  # Medium output for 6-8 takeaways
            top_p=0.92,
        )

    def get_messages(
        self, session: SessionModel, context: Dict[str, Any]
    ) -> List[BaseMessage]:
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

Generierte Zusammenfassung:
{context.get('summary', '')}

Transkript:
{context.get('transcription', '')}

Extrahiere nun die Key Takeaways:"""
            ),
        ]

    def process_response(self, response: Any) -> Dict[str, Any]:
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
from app.workflows.execution_context import StepRegistry
_key_takeaways_step = KeyTakeawaysStep()
StepRegistry.register(_key_takeaways_step)
