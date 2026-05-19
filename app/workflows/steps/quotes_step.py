"""Quotes step - curates a collection of direct quotes from talk and discussion sessions."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from rapidfuzz import fuzz

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()

# Minimum fuzzy similarity score (0-100) for a quote to be considered verified
_FUZZY_THRESHOLD = 65
# Maximum number of final quotes to keep after verification
_MAX_VERIFIED_QUOTES = 3


def _extract_quote_texts(raw: str) -> list[str]:
    """Extract quote strings from Markdown blockquote lines.

    Handles both German quote markers („“) and plain ASCII variants.
    Returns only fragments longer than 10 characters to filter stubs.
    """
    results = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith(">"):
            continue
        # Strip the leading > and whitespace
        text = stripped.lstrip(">").strip()
        # Strip surrounding quote markers (German, curly, straight)
        text = text.strip("\u201e\u201c\u201d\"\u00ab\u00bb'")
        if len(text) > 10:
            results.append(text)
    return results


def _is_quote_verified(quote: str, transcription: str) -> bool:
    """Return True if the quote appears in the transcription with sufficient similarity.

    Uses rapidfuzz partial_ratio which finds the best-matching substring in the
    (potentially much longer) transcription text.
    """
    return fuzz.partial_ratio(quote, transcription) >= _FUZZY_THRESHOLD


class QuotesStep(LLMStep):
    """
    Curates 3-5 direct quotes from talk and discussion sessions.

    Used for Input talks and Discussion sessions. Runs after the summary step
    so it can use the summary to identify the most relevant topics for quote selection.

    Input: Session metadata + transcription + summary
    Output: Markdown blockquote list of 3-5 direct original quotes

    Warning: LLMs may paraphrase or alter quotes. The prompt explicitly instructs
    the model to use exact text from the transcript. Temperature is set very low to
    reduce drift, but human review of generated quotes is still recommended.
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "quotes"

    @property
    def context_requirements(self) -> list[str]:
        """Requires transcription and summary."""
        return ["transcription", "summary"]

    def get_model_config(self) -> ChatModelConfig:
        """Quotes need exact reproduction - use lowest temperature to reduce drift."""
        return ChatModelConfig(
            model="mistral-large-3-675b-instruct-2512",
            temperature=0.1,  # Very low: quotes must be as close to verbatim as possible
            max_tokens=1000,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate quotes selection messages."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unbekannte Referent:innen"

        return [
            SystemMessage(
                content="""Du kuratierst eine Sammlung der prägnantesten Zitate aus Bildungsveranstaltungen.

Deine Aufgabe ist es, 3-5 direkte Originalzitate aus dem Transkript auszuwählen. Gute Zitate:
- Fassen eine Kernaussage pointiert zusammen
- Sind in sich verständlich (kein Kontext nötig)
- Haben eine gewisse sprachliche Qualität oder Eindringlichkeit
- Ergänzen die Zusammenfassung, ohne sie zu wiederholen

Kritisch: Übernimm Zitate ausschließlich wortgetreu aus dem Transkript. Verändere keine Formulierungen, ergänze keine Wörter und füge keine Zitate hinzu, die nicht im Transkript vorkommen.

Gib die Zitate als Markdown-Blockquote-Liste zurück.

Format:
> „Direktes Zitat aus dem Transkript."

> „Weiteres Zitat." """
            ),
            HumanMessage(
                content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}

Zusammenfassung (zur Orientierung über Kernthemen):
{context.get('summary', '')}

Transkript (Quelle der Zitate):
{context.get('transcription', '')}

Wähle nun 3-5 prägnante Originalzitate aus dem Transkript aus."""
            ),
        ]

    async def _invoke_and_process(
        self, session: SessionModel, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Invoke LLM, then fuzzy-verify generated quotes against the original transcription.

        Flow:
        1. Ask LLM to generate 3-5 quote candidates.
        2. Extract individual quote strings from the blockquote-formatted response.
        3. Fuzzy-match each candidate against the transcription (threshold: 85).
        4. Keep up to 2 verified quotes; return empty string if none pass (graceful fallback).
        """
        messages = self.get_messages(session, context)
        response = await self.get_model().ainvoke(messages)
        raw_output = response.content if hasattr(response, "content") else str(response)

        transcription = context.get("transcription", "")
        candidates = _extract_quote_texts(raw_output)

        logger.debug(
            "quotes_step_candidates_generated",
            session_id=session.id,
            candidates_count=len(candidates),
        )

        if not candidates or not transcription:
            logger.warning(
                "quotes_step_skipping_verification",
                session_id=session.id,
                reason="no candidates or no transcription",
            )
            return self.process_response(response)

        verified = [q for q in candidates if _is_quote_verified(q, transcription)]

        # Log unverified quotes for debugging
        unverified = [q for q in candidates if not _is_quote_verified(q, transcription)]
        if unverified:
            logger.warning(
                "quotes_step_verification_failures",
                session_id=session.id,
                unverified_count=len(unverified),
                unverified_quotes=unverified,
                transcription_length=len(transcription),
                threshold=_FUZZY_THRESHOLD,
            )

        logger.info(
            "quotes_step_verification_result",
            session_id=session.id,
            candidates=len(candidates),
            verified=len(verified),
        )

        if not verified:
            logger.warning(
                "quotes_step_no_verified_quotes_fallback",
                session_id=session.id,
            )
            return {
                "content": "",
                "content_type": "markdown",
                "meta_info": {
                    "model": self.get_model_config().model,
                    "type": "generated_quotes",
                    "verified": 0,
                    "candidates": len(candidates),
                    "fallback": True,
                },
            }

        top_quotes = verified[:_MAX_VERIFIED_QUOTES]
        formatted = "\n\n".join(f"> \u201e{q}\u201c" for q in top_quotes)

        return {
            "content": formatted,
            "content_type": "markdown",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_quotes",
                "verified": len(top_quotes),
                "candidates": len(candidates),
            },
        }

    def process_response(self, response: Any) -> dict[str, Any]:
        """Process LLM response into quotes output."""
        quotes = response.content if hasattr(response, "content") else str(response)
        # Extract markdown from code fence wrappers if present
        quotes = self._extract_markdown_from_code_fences(quotes)

        return {
            "content": quotes,
            "content_type": "markdown",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_quotes",
            },
        }


# Auto-register this step when imported
_quotes_step = QuotesStep()
StepRegistry.register(_quotes_step)
