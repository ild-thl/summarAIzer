"""Sondercluster step - generates cluster-specific highlight sentences for special-topic sessions."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()

# Canonical cluster keys (lowercase) to cluster config
SONDERCLUSTER_CONFIGS: dict[str, dict[str, str]] = {
    "fringe": {
        "label": "🚨 Fringe",
        "prompt": (
            "Was war die kontraintuitive These, die überraschende Methodenkombination oder der "
            "unerwartete Moment dieser Session? Formuliere 1-2 prägnante Sätze, die genau diesen "
            "Fringe-Charakter auf den Punkt bringen."
        ),
    },
    "fail & learn": {
        "label": "🔄 Fail & Learn",
        "prompt": (
            "Was war der zentrale Misserfolg oder die Herausforderung in dieser Session, "
            "und was war das daraus gezogene Learning? Formuliere 1-2 prägnante Sätze, "
            "die Fail und Learning klar benennen."
        ),
    },
    "global perspectives": {
        "label": "🌍 Global Perspectives",
        "prompt": (
            "Welchen internationalen Kontext, welche Fallbeispiele aus anderen Ländern oder "
            "welche Transferideen (z.B. aus anderen Bildungssystemen) enthielt diese Session? "
            "Formuliere 1-2 Sätze, die den globalen Blickwinkel hervorheben."
        ),
    },
    "student voices": {
        "label": "📚 Student Voices",
        "prompt": (
            "Welche Perspektive oder welchen inhaltlichen Beitrag haben Studierende in dieser "
            "Session eingebracht? Formuliere 1-2 Sätze, die den studentischen Beitrag sichtbar machen."
        ),
    },
    "ecological sustainability": {
        "label": "🌱 Ökologische Nachhaltigkeit",
        "prompt": (
            "Welche Bezüge zu ökologischer Nachhaltigkeit, klimabezogenen Themen oder "
            "nachhaltigen Methoden enthielt diese Session? Formuliere 1-2 Sätze, "
            "die den Nachhaltigkeitsbezug konkret benennen."
        ),
    },
}

# Maps all recognized tag variants (lowercase, stripped) to canonical cluster keys
SONDERCLUSTER_TAG_MAP: dict[str, str] = {
    "fringe": "fringe",
    "fail & learn": "fail & learn",
    "fail and learn": "fail & learn",
    "global perspectives": "global perspectives",
    "student voices": "student voices",
    "ecological sustainability": "ecological sustainability",
    "ökologische nachhaltigkeit": "ecological sustainability",
    "nachhaltigkeit": "ecological sustainability",
}


def get_matched_clusters(tags: list[str]) -> list[str]:
    """Return canonical cluster keys for all Sondercluster tags present in the tag list."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = SONDERCLUSTER_TAG_MAP.get(tag.lower().strip())
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


class SonderclusterStep(LLMStep):
    """
    Generates 1-2 cluster-specific highlight sentences for sessions tagged with a Sondercluster.

    Runs after the summary step, in parallel with other post-summary steps.
    Only added to the workflow execution when the session carries at least one
    of the five Sondercluster tags: Fringe, Fail & Learn, Global Perspectives,
    Student Voices, Ecological Sustainability.

    Input: Session metadata + summary
    Output: One short paragraph per matching cluster (1-2 sentences each)
    """

    @property
    def identifier(self) -> str:
        return "sondercluster"

    @property
    def context_requirements(self) -> list[str]:
        return ["summary"]

    def get_model_config(self) -> ChatModelConfig:
        return ChatModelConfig(
            model="mistral-large-3-675b-instruct-2512",
            temperature=0.5,
            max_tokens=400,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate cluster-specific highlight messages."""
        tags = session.tags or []
        matched = get_matched_clusters(tags)

        if not matched:
            # Should not happen if routing is correct, but handle gracefully
            logger.warning(
                "sondercluster_step_no_matching_tags",
                session_id=session.id,
                tags=tags,
            )
            matched = []

        cluster_instructions = "\n\n".join(
            f"**{SONDERCLUSTER_CONFIGS[key]['label']}**\n{SONDERCLUSTER_CONFIGS[key]['prompt']}"
            for key in matched
        )

        return [
            SystemMessage(
                content="""Du ergänzt Dokumentationen von Bildungsveranstaltungen um kurze Cluster-Highlights.

Für jeden angefragten Sondercluster formulierst du 1-2 prägnante Sätze, die den spezifischen Charakter dieses Clusters in der Session hervorheben. Die Sätze sollen informativ und konkret sein - keine allgemeinen Aussagen.

Gib für jeden Cluster einen kurzen Absatz aus, eingeleitet durch das Label mit Emoji (fett, z.B. **🚨 Fringe**), gefolgt von deinen 1-2 Sätzen. Keine weiteren Einleitungen oder Erklärungen."""
            ),
            HumanMessage(content=f"""Session: {session.title}
Referent:innen: {", ".join(session.speakers) if session.speakers else "Unbekannt"}

Zusammenfassung:
{context.get("summary", "")}

Bitte formuliere nun die Cluster-Highlights:

{cluster_instructions}"""),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into sondercluster output."""
        content = response.content if hasattr(response, "content") else str(response)
        # Extract markdown from code fence wrappers if present
        content = self._extract_markdown_from_code_fences(content)

        return {
            "content": content,
            "content_type": "markdown",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_sondercluster",
            },
        }


StepRegistry.register(SonderclusterStep())
