"""Unit tests for wordcloud keyword extraction heuristics."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.workflows.steps.wordcloud_step import (
    WordcloudStep,
    _compute_word_frequencies,
    _extract_json_array_from_response,
)


def test_wordcloud_prefers_noun_like_terms_and_filters_fillers() -> None:
    text = (
        "Ähm danke, heute finde ich das Thema wichtig. "
        "An den Hochschulen sprechen Studierende über Machtmissbrauch. "
        "Bei Hochschulen und Hochschulen geht es um Machtmissbrauch und Perspektive. "
        "Sozusagen ganz viele Leute machen das, aber Hochschulen bleiben zentral."
    )

    pairs = _compute_word_frequencies(text, top_n=20)
    words = [word for word, _ in pairs]

    assert "Hochschulen" in words
    assert "Machtmissbrauch" in words
    assert "ähm" not in [word.lower() for word in words]
    assert "danke" not in [word.lower() for word in words]
    assert "heute" not in [word.lower() for word in words]


def test_wordcloud_preserves_original_casing_for_display() -> None:
    text = (
        "Hochschulen und Hochschulen sind zentral. "
        "hochschulen wird manchmal klein geschrieben, Hochschulen bleibt korrekt."
    )

    pairs = _compute_word_frequencies(text, top_n=10)

    assert pairs[0][0] == "Hochschulen"


def test_wordcloud_falls_back_when_noun_candidates_are_sparse() -> None:
    text = "innovation platform architecture pipeline feedback innovation platform"

    pairs = _compute_word_frequencies(text, top_n=10)
    words = [word.lower() for word, _ in pairs]

    assert "innovation" in words
    assert "platform" in words


def test_extract_json_array_from_code_fence() -> None:
    content = '```json\n["Hochschulen", "Machtmissbrauch"]\n```'

    parsed = _extract_json_array_from_response(content)

    assert parsed == ["Hochschulen", "Machtmissbrauch"]


@pytest.mark.asyncio
async def test_wordcloud_rerank_failure_falls_back_to_top_25() -> None:
    step = WordcloudStep()
    step.get_model = Mock(side_effect=RuntimeError("llm unavailable"))

    # 26 alphabetic content words after stopword filtering to ensure fallback list is 25 long
    terms = " ".join(
        [
            "Alpha",
            "Beta",
            "Gamma",
            "Delta",
            "Epsilon",
            "Zeta",
            "Eta",
            "Theta",
            "Iota",
            "Kappa",
            "Lambda",
            "Mueller",
            "Nuance",
            "Omikron",
            "Pirol",
            "Rho",
            "Sigma",
            "Tau",
            "Uranus",
            "Phi",
            "Chi",
            "Psi",
            "Omega",
            "Atlas",
            "Boreal",
            "Cobalt",
        ]
    )
    transcription = f"In diesem Panel diskutieren wir {terms}"

    result = await step._generate(session_id=1, db=Mock(), context={"transcription": transcription})
    pairs = json.loads(result["content"])

    assert len(pairs) == 25
    assert result["meta_info"]["rerank_applied"] is False


@pytest.mark.asyncio
async def test_wordcloud_rerank_success_uses_model_selection() -> None:
    step = WordcloudStep()

    terms = [
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
        "Epsilon",
        "Zeta",
        "Eta",
        "Theta",
        "Iota",
        "Kappa",
        "Lambda",
        "Mueller",
        "Nuance",
        "Omikron",
        "Pirol",
        "Rho",
        "Sigma",
        "Tau",
        "Uranus",
        "Phi",
        "Chi",
        "Psi",
        "Omega",
        "Atlas",
        "Boreal",
        "Cobalt",
    ]
    transcription = "In diesem Panel diskutieren wir " + " ".join(terms)

    llm_words = terms[:25][::-1]
    mock_response = Mock()
    mock_response.content = json.dumps(llm_words, ensure_ascii=False)

    mock_model = Mock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    step.get_model = Mock(return_value=mock_model)

    result = await step._generate(session_id=1, db=Mock(), context={"transcription": transcription})
    pairs = json.loads(result["content"])

    assert len(pairs) == 25
    assert pairs[0][0] == llm_words[0]
    assert result["meta_info"]["rerank_applied"] is True
