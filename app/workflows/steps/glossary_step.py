"""Glossary step - extracts specialized terms with one-line definitions."""

import json
import re
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()

_GLOSSARY_MIN_ENTRIES = 5
_GLOSSARY_MAX_ENTRIES = 8


def _extract_json_array_candidate(content: str) -> str:
    raw = content.strip()

    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()

    if not raw.startswith("["):
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            raw = match.group(0)

    return raw


def _loads_with_repairs(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Repair common LLM JSON issues before giving up.
        repaired = re.sub(r"}\s*\n\s*{", "},\n{", raw)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return json.loads(repaired)


def _extract_term_definition(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("term", "")).strip(), str(item.get("definition", "")).strip()

    if isinstance(item, list | tuple) and len(item) >= 2:
        return str(item[0]).strip(), str(item[1]).strip()

    return "", ""


def _extract_structured_items(content: str) -> list[dict[str, str]]:
    """Parse a JSON array of glossary items from model output."""
    raw = _extract_json_array_candidate(content)
    parsed = _loads_with_repairs(raw)

    if not isinstance(parsed, list):
        return []

    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in parsed:
        term, definition = _extract_term_definition(item)

        if not term or not definition:
            continue

        key = term.lower()
        if key in seen:
            continue

        seen.add(key)
        items.append({"term": term, "definition": definition})

    return items


class GlossaryStep(LLMStep):
    """Extract 5-8 specialized terms with short definitions."""

    @property
    def identifier(self) -> str:
        return "glossary"

    @property
    def context_requirements(self) -> list[str]:
        return ["transcription", "summary"]

    def get_model_config(self) -> ChatModelConfig:
        return ChatModelConfig(
            model="gemma-4-31b-it",
            temperature=0.1,
            max_tokens=1200,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        speakers = ", ".join(session.speakers) if session.speakers else "Unbekannt"
        transcription = context.get("transcription", "")
        summary = context.get("summary", "")
        slide_markdown = context.get("slide_markdown", "")
        slide_block = (
            f"\nFolieninhalt (Docling/PDF-Extraktion):\n{slide_markdown}\n"
            if slide_markdown
            else ""
        )
        description_block = (
            f"\n\nSession-Beschreibung:\n{session.description}\n" if session.description else ""
        )

        return [
            SystemMessage(
                content="""Du extrahierst ein kompaktes Fachglossar aus technischen oder domänenspezifischen Vorträgen.

Deine Aufgabe:
- Wähle 4-8 spezialisierte Begriffe, die für das Verständnis der Session wirklich wichtig sind.
- Bevorzuge Fachbegriffe, Projekt-/Tool-Namen, Methoden, Akronyme und domänenspezifische Konzepte.
- Gib zu jedem Begriff eine kurze Definition in genau einem Satz.
- Vermeide allgemeine Wörter oder offensichtliche Füllbegriffe.
- Nutze ausschließlich Begriffe aus den Folien und der Session-Beschreibung. Erfinde keine Begriffe oder Definitionen.
- Die Transkription kann fehlerhaft sein, vertraue also mehr der Beschreibung und den Folien, wenn es um die Schreibweise von besimmten Akronymen oder Fachbegriffen geht.

Gib AUSSCHLIESSLICH ein JSON-Array zurück. Jedes Element muss ein Objekt mit den Schlüsseln "term" und "definition" sein."""
            ),
            HumanMessage(
                content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}

{description_block}

Zusammenfassung:
{summary}
{slide_block}

Transkript:
{transcription}

Extrahiere jetzt das Fachglossar als JSON-Array:"""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        content = response.content if hasattr(response, "content") else str(response)
        parse_failed = False

        try:
            items = _extract_structured_items(str(content))
        except Exception as exc:
            logger.warning("glossary_step_parse_failed", error=str(exc))
            items = []
            parse_failed = True

        if len(items) < _GLOSSARY_MIN_ENTRIES:
            return {
                "content": "",
                "content_type": "json",
                "persist": False,
                "meta_info": {
                    "model": self.get_model_config().model,
                    "type": "generated_glossary",
                    "count": len(items),
                    "min_entries": _GLOSSARY_MIN_ENTRIES,
                    "max_entries": _GLOSSARY_MAX_ENTRIES,
                    "skipped": True,
                    "reason": "parse_failed" if parse_failed else "insufficient_entries",
                },
            }

        selected = items[:_GLOSSARY_MAX_ENTRIES]
        content_json = json.dumps(selected, ensure_ascii=False)

        return {
            "content": content_json,
            "content_type": "json",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_glossary",
                "count": len(selected),
                "min_entries": _GLOSSARY_MIN_ENTRIES,
                "max_entries": _GLOSSARY_MAX_ENTRIES,
            },
        }


StepRegistry.register(GlossaryStep())
