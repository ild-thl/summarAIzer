"""Q&A step - extracts audience questions and concise answers as structured JSON."""

import json
import re
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()
settings = get_settings()
_QNA_MIN_ENTRIES = 2
_QNA_MAX_ENTRIES = 6


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
        # Try to recover common LLM JSON issues:
        # - missing comma between object literals
        # - trailing commas before closing braces/brackets
        repaired = re.sub(r"}\s*\n\s*{", "},\n{", raw)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return json.loads(repaired)


def _extract_question_answer(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("question", "")).strip(), str(item.get("answer", "")).strip()

    if isinstance(item, list | tuple) and len(item) >= 2:
        return str(item[0]).strip(), str(item[1]).strip()

    return "", ""


def _extract_structured_items(content: str) -> list[dict[str, str]]:
    """Parse a JSON array of question-answer objects from model output."""
    raw = _extract_json_array_candidate(content)
    parsed = _loads_with_repairs(raw)

    if not isinstance(parsed, list):
        return []

    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in parsed:
        question, answer = _extract_question_answer(item)

        if not question or not answer:
            continue

        key = (question.lower(), answer.lower())
        if key in seen:
            continue

        seen.add(key)
        items.append({"question": question, "answer": answer})

    return items


class QnAStep(LLMStep):
    """Extract audience questions and answers as a compact FAQ section."""

    @property
    def identifier(self) -> str:
        return "qna"

    @property
    def context_requirements(self) -> list[str]:
        return ["transcription", "summary"]

    def get_model_config(self) -> ChatModelConfig:
        return ChatModelConfig(
            model=settings.llm_model_medium,
            temperature=0.1,
            max_tokens=1400,
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

        return [
            SystemMessage(
                content="""Du extrahierst Audience Q&A aus Transkripten von Vorträgen und Diskussionen.

Deine Aufgabe:
- Identifiziere 2-6 wirklich unterschiedliche Fragen aus der Publikumsrunde oder dem Diskussionsblock, die während der Veranstaltung beantwortet werden konnte.
- Formuliere zu jeder Frage eine kurze, präzise Antwort in 1-3 Sätzen, basierend auf den Informationen aus der Transkription und den Folieninhalten.
- Ignoriere rhetorische oder unklare Fragen.
- Nutze ausschließlich Informationen aus dem verfügbaren Material.

Gib AUSSCHLIESSLICH ein JSON-Array zurück. Jedes Element muss ein Objekt mit den Schlüsseln "question" und "answer" sein."""
            ),
            HumanMessage(
                content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}

Zusammenfassung:
{summary}

{slide_block}

Transkript:
{transcription}

Extrahiere jetzt die Audience-Fragen und kurzen Antworten als JSON-Array:"""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        content = response.content if hasattr(response, "content") else str(response)
        parse_failed = False

        try:
            items = _extract_structured_items(str(content))
        except Exception as exc:
            logger.warning("qna_step_parse_failed", error=str(exc))
            items = []
            parse_failed = True

        if len(items) < _QNA_MIN_ENTRIES:
            return {
                "content": "",
                "content_type": "json",
                "persist": False,
                "meta_info": {
                    "model": self.get_model_config().model,
                    "type": "generated_qna",
                    "count": len(items),
                    "min_entries": _QNA_MIN_ENTRIES,
                    "max_entries": _QNA_MAX_ENTRIES,
                    "skipped": True,
                    "reason": "parse_failed" if parse_failed else "insufficient_entries",
                },
            }

        selected = items[:_QNA_MAX_ENTRIES]
        content_json = json.dumps(selected, ensure_ascii=False)

        return {
            "content": content_json,
            "content_type": "json",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_qna",
                "count": len(selected),
                "min_entries": _QNA_MIN_ENTRIES,
                "max_entries": _QNA_MAX_ENTRIES,
            },
        }


StepRegistry.register(QnAStep())
