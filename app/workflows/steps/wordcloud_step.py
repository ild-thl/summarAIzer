"""Word cloud step - NLP frequency analysis of session transcription."""

import json
import math
import re
from collections import Counter
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.config.wordcloud_stopwords import ALL_STOP_WORDS
from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()
settings = get_settings()

_WORDCLOUD_RERANK_CANDIDATES = 50
_WORDCLOUD_FALLBACK_WORDS = 25
_WORDCLOUD_RERANK_CONTEXT_CANDIDATES = "wordcloud_rerank_candidates"
_WORDCLOUD_RERANK_CONTEXT_SNIPPET = "wordcloud_rerank_snippet"

# Matches words with at least 3 characters (includes German umlauts)
_TOKEN_RE = re.compile(r"\b[a-zA-ZäöüÄÖÜßàáâãèéêëìíîïòóôõùúûýÀÁÂÃÈÉÊËÌÍÎÏÒÓÔÕÙÚÛÝ]{3,}\b")


def _is_likely_sentence_start(text: str, token_start: int) -> bool:
    """Best-effort check to avoid sentence-initial capitalization noise."""
    idx = token_start - 1
    while idx >= 0 and text[idx].isspace():
        idx -= 1

    while idx >= 0 and text[idx] in {'"', "'", "(", "[", "{", "<", "»", "«", "“", "”", "‘", "’"}:
        idx -= 1

    if idx < 0:
        return True

    return text[idx] in {".", "!", "?", ";", "\n"}


def _is_acronym(token: str) -> bool:
    """Return True for acronym-like words such as AI, EU, BMBF."""
    letters = [char for char in token if char.isalpha()]
    return len(letters) >= 2 and all(char.isupper() for char in letters)


def _is_noun_candidate(token: str, text: str, token_start: int) -> bool:
    """Approximate noun detection for German transcripts without external NLP deps."""
    if _is_acronym(token):
        return True

    if not token[0].isupper():
        return False

    return not _is_likely_sentence_start(text, token_start)


def _pick_display_form(forms: Counter[str]) -> str:
    """Choose the most representative original casing for output."""
    return sorted(forms.items(), key=lambda item: (-item[1], item[0].islower(), item[0]))[0][0]


def _compute_word_frequencies(text: str, top_n: int = 70) -> list[tuple[str, int]]:
    """Tokenize, filter stop words, return top-N (word, scaled_weight) pairs.

    Weights are log-scaled to 10-100 for better visual spread in word clouds.
    """
    all_tokens: list[str] = []
    noun_tokens: list[str] = []
    display_forms: dict[str, Counter[str]] = {}

    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        normalized = token.lower()

        if normalized in ALL_STOP_WORDS:
            continue

        all_tokens.append(normalized)
        display_forms.setdefault(normalized, Counter())[token] += 1

        if _is_noun_candidate(token, text, match.start()):
            noun_tokens.append(normalized)

    tokens = noun_tokens if len(set(noun_tokens)) >= 8 else all_tokens

    counts = Counter(tokens)
    if not counts:
        return []

    most_common = counts.most_common(top_n)
    if len(most_common) == 1:
        return [(most_common[0][0], 50)]

    max_count = most_common[0][1]

    result = []
    for word, count in most_common:
        weight = int(10 + math.log(count) / math.log(max_count) * 90) if max_count > 1 else 50

        display_word = _pick_display_form(display_forms[word]) if word in display_forms else word
        result.append((display_word, max(10, weight)))

    return result


def _extract_json_array_from_response(content: str) -> list[str]:
    """Parse a JSON array from model output and return string items only."""
    raw = content.strip()

    if raw.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()

    if not raw.startswith("["):
        bracket_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if bracket_match:
            raw = bracket_match.group(0)

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []

    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]


class WordcloudStep(LLMStep):
    """Generate wordcloud keywords from transcription and rerank with LLM."""

    @property
    def identifier(self) -> str:
        return "wordcloud"

    @property
    def context_requirements(self) -> list[str]:
        return ["transcription"]

    def get_model_config(self) -> ChatModelConfig:
        return ChatModelConfig(
            model=settings.llm_model_small,
            temperature=0.1,
            max_tokens=500,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        candidates = context.get(_WORDCLOUD_RERANK_CONTEXT_CANDIDATES, [])
        snippet = context.get(_WORDCLOUD_RERANK_CONTEXT_SNIPPET, "")

        if not isinstance(candidates, list):
            candidates = []
        if not isinstance(snippet, str):
            snippet = ""

        return [
            SystemMessage(
                content=(
                    "Du priorisierst relevante Begriffe fuer eine deutsche Wortwolke. "
                    "Waehle genau 25 Begriffe aus einer gegebenen Kandidatenliste. "
                    "Regeln:\n"
                    "- Nur Begriffe aus der Kandidatenliste verwenden\n"
                    "- Genau 25 Begriffe liefern\n"
                    "- Keine Duplikate\n"
                    "- Keine Erklaerung, nur JSON-Array aus Strings"
                )
            ),
            HumanMessage(
                content=(
                    f"Sessiontitel: {session.title}\n\n"
                    f"Kandidaten (Top {_WORDCLOUD_RERANK_CANDIDATES}):\n"
                    f"{json.dumps(candidates, ensure_ascii=False)}\n\n"
                    "Transkript-Auszug:\n"
                    f"{snippet}\n\n"
                    "Gib jetzt genau 25 relevante Begriffe als JSON-Array zurueck."
                )
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        content = response.content if hasattr(response, "content") else str(response)
        try:
            ranked_words = _extract_json_array_from_response(str(content))
        except Exception:
            ranked_words = []
        return {"ranked_words": ranked_words}

    async def _rerank_top_words_with_llm(
        self,
        session: SessionModel,
        session_id: int,
        transcription: str,
        candidates: list[str],
    ) -> list[str] | None:
        """Rerank top candidates via LLM; returns None when unusable."""
        if not candidates:
            return None

        rerank_context = {
            _WORDCLOUD_RERANK_CONTEXT_CANDIDATES: candidates,
            _WORDCLOUD_RERANK_CONTEXT_SNIPPET: transcription[:12000],
        }

        try:
            result = await self._invoke_and_process(session, rerank_context)
            ranked_words = result.get("ranked_words", [])

            if not isinstance(ranked_words, list) or len(ranked_words) < _WORDCLOUD_FALLBACK_WORDS:
                logger.warning(
                    "wordcloud_step_rerank_too_few_terms",
                    session_id=session_id,
                    returned_count=len(ranked_words) if isinstance(ranked_words, list) else 0,
                    expected=_WORDCLOUD_FALLBACK_WORDS,
                )
                return None

            return [word for word in ranked_words if isinstance(word, str)]

        except Exception as exc:
            logger.warning(
                "wordcloud_step_rerank_failed",
                session_id=session_id,
                error=str(exc),
            )
            return None

    async def _generate(
        self, session_id: int, db: Session, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Tokenize transcription, rerank top candidates, return scaled word list."""
        transcription = context.get("transcription", "")

        if not isinstance(transcription, str) or len(transcription.strip()) < 50:
            logger.warning(
                "wordcloud_step_insufficient_transcription",
                session_id=session_id,
                length=len(transcription) if isinstance(transcription, str) else 0,
            )
            return {
                "content": "[]",
                "content_type": "json_array",
                "meta_info": {
                    "type": "word_frequency_cloud",
                    "word_count": 0,
                    "skipped": True,
                },
            }

        word_pairs = _compute_word_frequencies(transcription, top_n=70)
        fallback_pairs = word_pairs[:_WORDCLOUD_FALLBACK_WORDS]
        candidate_words = [word for word, _ in word_pairs[:_WORDCLOUD_RERANK_CANDIDATES]]

        selected_pairs = fallback_pairs
        rerank_applied = False

        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        reranked_words: list[str] | None = None
        if session is not None:
            reranked_words = await self._rerank_top_words_with_llm(
                session=session,
                session_id=session_id,
                transcription=transcription,
                candidates=candidate_words,
            )
        else:
            logger.warning("wordcloud_step_session_not_found_for_rerank", session_id=session_id)

        if reranked_words:
            pairs_by_normalized = {word.lower(): (word, weight) for word, weight in word_pairs}
            reranked_pairs: list[tuple[str, int]] = []
            seen: set[str] = set()

            for ranked_word in reranked_words:
                normalized = ranked_word.lower().strip()
                if normalized in seen:
                    continue

                pair = pairs_by_normalized.get(normalized)
                if pair is None:
                    continue

                reranked_pairs.append(pair)
                seen.add(normalized)

                if len(reranked_pairs) == _WORDCLOUD_FALLBACK_WORDS:
                    break

            if len(reranked_pairs) >= _WORDCLOUD_FALLBACK_WORDS:
                selected_pairs = reranked_pairs
                rerank_applied = True
            else:
                logger.warning(
                    "wordcloud_step_rerank_invalid_terms",
                    session_id=session_id,
                    valid_count=len(reranked_pairs),
                    expected=_WORDCLOUD_FALLBACK_WORDS,
                )

        logger.info(
            "wordcloud_step_completed",
            session_id=session_id,
            unique_words=len(selected_pairs),
            top_word=selected_pairs[0][0] if selected_pairs else None,
            rerank_applied=rerank_applied,
        )

        return {
            "content": json.dumps(selected_pairs, ensure_ascii=False),
            "content_type": "json_array",
            "meta_info": {
                "type": "word_frequency_cloud",
                "word_count": len(selected_pairs),
                "rerank_applied": rerank_applied,
                "candidate_count": min(len(word_pairs), _WORDCLOUD_RERANK_CANDIDATES),
                "fallback_word_count": _WORDCLOUD_FALLBACK_WORDS,
            },
        }


# Auto-register this step when imported
_wordcloud_step = WordcloudStep()
StepRegistry.register(_wordcloud_step)
