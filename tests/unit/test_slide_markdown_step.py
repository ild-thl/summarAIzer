"""Unit tests for slide markdown extraction step."""

from unittest.mock import Mock, patch

import pytest

from app.database.models import GeneratedContent, WorkflowExecution, WorkflowExecutionStatus
from app.workflows.steps.slide_markdown_step import SlideMarkdownStep


@pytest.mark.asyncio
async def test_slide_markdown_step_skips_when_slide_deck_missing(test_db, sample_session):
    """Step should no-op without slide_deck payload."""
    step = SlideMarkdownStep()
    step._save_to_db = Mock()

    with patch("app.database.connection.SessionLocal") as mock_session_local:
        mock_session_local.return_value = test_db
        result = await step.execute(session_id=sample_session.id, execution_id=1, context={})

    assert result == {}
    step._save_to_db.assert_not_called()


@pytest.mark.asyncio
async def test_slide_markdown_step_generates_and_persists_markdown(test_db, sample_session):
    """Step should download PDF from S3, convert via Docling, and persist markdown."""
    step = SlideMarkdownStep()
    workflow_execution = WorkflowExecution(
        session_id=sample_session.id,
        target="talk_workflow",
        status=WorkflowExecutionStatus.RUNNING,
        triggered_by="user_triggered",
    )
    test_db.add(workflow_execution)
    test_db.commit()
    test_db.refresh(workflow_execution)

    with (
        patch("app.database.connection.SessionLocal") as mock_session_local,
        patch("app.workflows.steps.slide_markdown_step.get_s3_slide_service") as mock_get_s3,
        patch("app.workflows.steps.slide_markdown_step.DoclingService") as mock_docling_cls,
        patch("app.workflows.steps.slide_markdown_step.PDFTextService") as mock_pdf_cls,
    ):
        mock_session_local.return_value = test_db

        mock_s3 = Mock()
        mock_s3.download_slide.return_value = b"%PDF-1.7 test"
        mock_get_s3.return_value = mock_s3

        mock_docling = Mock()
        mock_docling.convert_pdf_to_markdown.return_value = {
            "success": True,
            "markdown": "# Slides\n\nExtracted content",
            "response_type": "MARKDOWN",
            "image_count": 0,
        }
        mock_docling_cls.return_value = mock_docling
        mock_pdf_cls.return_value.extract_markdown.return_value = {
            "success": True,
            "markdown": "# Should not be used",
        }

        result = await step.execute(
            session_id=sample_session.id,
            execution_id=workflow_execution.id,
            context={
                "slide_deck": '{"s3_key":"content/summaraizer/slides/session_1/deck.pdf","filename":"deck.pdf"}'
            },
        )

    assert result["slide_markdown"].startswith("# Slides")
    stored = (
        test_db.query(GeneratedContent)
        .filter_by(
            session_id=sample_session.id,
            identifier="slide_markdown",
        )
        .first()
    )
    assert stored is not None
    assert stored.content_type == "markdown"
    assert stored.meta_info["source"] == "docling"


@pytest.mark.asyncio
async def test_slide_markdown_step_falls_back_to_pypdf_for_large_files(test_db, sample_session):
    """Large PDFs should bypass Docling and use local pypdf extraction."""
    step = SlideMarkdownStep()
    workflow_execution = WorkflowExecution(
        session_id=sample_session.id,
        target="talk_workflow",
        status=WorkflowExecutionStatus.RUNNING,
        triggered_by="user_triggered",
    )
    test_db.add(workflow_execution)
    test_db.commit()
    test_db.refresh(workflow_execution)

    large_bytes = b"0" * (step.docling_max_bytes + 1024)

    with (
        patch("app.database.connection.SessionLocal") as mock_session_local,
        patch("app.workflows.steps.slide_markdown_step.get_s3_slide_service") as mock_get_s3,
        patch("app.workflows.steps.slide_markdown_step.DoclingService") as mock_docling_cls,
        patch("app.workflows.steps.slide_markdown_step.PDFTextService") as mock_pdf_cls,
    ):
        mock_session_local.return_value = test_db

        mock_s3 = Mock()
        mock_s3.download_slide.return_value = large_bytes
        mock_get_s3.return_value = mock_s3

        mock_docling = Mock()
        mock_docling.convert_pdf_to_markdown.return_value = {
            "success": True,
            "markdown": "# Not expected",
        }
        mock_docling_cls.return_value = mock_docling

        mock_pdf = Mock()
        mock_pdf.extract_markdown.return_value = {
            "success": True,
            "markdown": "# Fallback markdown",
            "page_count": 10,
            "char_count": 100,
            "truncated": False,
        }
        mock_pdf_cls.return_value = mock_pdf

        result = await step.execute(
            session_id=sample_session.id,
            execution_id=workflow_execution.id,
            context={
                "slide_deck": (
                    '{"s3_key":"content/summaraizer/slides/session_1/deck.pdf",'
                    f'"filename":"deck.pdf","size":{len(large_bytes)}'
                    "}"
                )
            },
        )

    assert result["slide_markdown"].startswith("# Fallback")
    mock_docling.convert_pdf_to_markdown.assert_not_called()

    stored = (
        test_db.query(GeneratedContent)
        .filter_by(
            session_id=sample_session.id,
            identifier="slide_markdown",
        )
        .first()
    )
    assert stored is not None
    assert stored.meta_info["source"] == "pypdf"


@pytest.mark.asyncio
async def test_slide_markdown_step_skips_when_file_is_too_large(test_db, sample_session):
    """Oversized PDFs should be skipped instead of failing and triggering retries."""
    step = SlideMarkdownStep()
    step._save_to_db = Mock()

    too_large_size = step.fallback_max_bytes + 1
    with patch("app.database.connection.SessionLocal") as mock_session_local:
        mock_session_local.return_value = test_db
        result = await step.execute(
            session_id=sample_session.id,
            execution_id=1,
            context={
                "slide_deck": (
                    '{"s3_key":"content/summaraizer/slides/session_1/deck.pdf",'
                    f'"filename":"deck.pdf","size":{too_large_size}'
                    "}"
                )
            },
        )

    assert result == {}
    step._save_to_db.assert_not_called()
