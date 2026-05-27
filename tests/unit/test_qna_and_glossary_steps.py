"""Unit tests for Q&A and glossary extraction steps."""

import json
from unittest.mock import Mock

from app.workflows.steps.glossary_step import GlossaryStep
from app.workflows.steps.qna_step import QnAStep


def test_qna_step_formats_collapsible_faq_list() -> None:
    step = QnAStep()
    response = Mock()
    response.content = json.dumps(
        [
            {
                "question": "Wie geht es weiter?",
                "answer": "Im nächsten Schritt folgt die Umsetzung.",
            },
            {
                "question": "Welche Tools sind relevant?",
                "answer": "Vor allem die Dokumentationspipeline.",
            },
        ],
        ensure_ascii=False,
    )

    result = step.process_response(response)
    parsed = json.loads(result["content"])

    assert result["content_type"] == "json"
    assert result["meta_info"]["count"] == 2
    assert isinstance(parsed, list)
    assert parsed[0]["question"] == "Wie geht es weiter?"
    assert parsed[1]["answer"] == "Vor allem die Dokumentationspipeline."


def test_qna_step_repairs_common_json_errors() -> None:
    step = QnAStep()
    response = Mock()
    response.content = """[
    {"question": "Frage 1", "answer": "Antwort 1"}
    {"question": "Frage 2", "answer": "Antwort 2"},
]"""

    result = step.process_response(response)
    parsed = json.loads(result["content"])

    assert result["content_type"] == "json"
    assert result.get("persist", True) is True
    assert len(parsed) == 2


def test_qna_step_skips_persistence_for_invalid_or_too_small_results() -> None:
    step = QnAStep()
    response = Mock()
    response.content = "not valid json"

    result = step.process_response(response)

    assert result["content"] == ""
    assert result["content_type"] == "json"
    assert result["persist"] is False
    assert result["meta_info"]["skipped"] is True
    assert result["meta_info"]["reason"] == "parse_failed"


def test_glossary_step_outputs_json_entries() -> None:
    step = GlossaryStep()
    response = Mock()
    response.content = json.dumps(
        [
            {"term": "LLM", "definition": "Ein großes Sprachmodell für Textgenerierung."},
            {"term": "Embeddings", "definition": "Vektorrepräsentationen für semantische Suche."},
            {"term": "RAG", "definition": "Retrieval-Augmented Generation mit Kontextabruf."},
            {"term": "Tokenizer", "definition": "Teilt Text in verarbeitbare Einheiten."},
            {"term": "Prompting", "definition": "Gezielte Eingabeanweisungen für das Modell."},
        ],
        ensure_ascii=False,
    )

    result = step.process_response(response)
    parsed = json.loads(result["content"])

    assert result["content_type"] == "json"
    assert result["meta_info"]["count"] == 5
    assert isinstance(parsed, list)
    assert parsed[0]["term"] == "LLM"
    assert parsed[2]["definition"] == "Retrieval-Augmented Generation mit Kontextabruf."


def test_glossary_step_repairs_common_json_errors() -> None:
    step = GlossaryStep()
    response = Mock()
    response.content = """[
    {"term": "LLM", "definition": "Sprachmodell"}
    {"term": "RAG", "definition": "Kontextabruf"},
    {"term": "Embeddings", "definition": "Vektorraum"},
    {"term": "Tokenizer", "definition": "Tokenisierung"},
    {"term": "Prompting", "definition": "Steuerung durch Eingabe"},
]"""

    result = step.process_response(response)
    parsed = json.loads(result["content"])

    assert result["content_type"] == "json"
    assert result.get("persist", True) is True
    assert len(parsed) == 5


def test_glossary_step_skips_when_too_few_terms() -> None:
    step = GlossaryStep()
    response = Mock()
    response.content = json.dumps(
        [
            {"term": "LLM", "definition": "Ein Sprachmodell."},
            {"term": "RAG", "definition": "Kontextabruf."},
            {"term": "Tokenizer", "definition": "Textsegmentierung."},
        ],
        ensure_ascii=False,
    )

    result = step.process_response(response)

    assert result["content"] == ""
    assert result["content_type"] == "json"
    assert result["persist"] is False
    assert result["meta_info"]["skipped"] is True
    assert result["meta_info"]["reason"] == "insufficient_entries"
