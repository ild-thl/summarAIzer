"""SlideMarkdownStep - extracts markdown from uploaded slide deck PDF via Docling."""

import json
from typing import Any

import structlog

from app.config.settings import get_settings
from app.crud import generated_content as content_crud
from app.services.docling_service import DoclingService
from app.services.pdf_text_service import PDFTextService
from app.services.s3_slide_service import get_s3_slide_service
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.base_step import WorkflowStep

logger = structlog.get_logger()


class SlideMarkdownStep(WorkflowStep):
    """Generate markdown from a slide deck PDF and persist it as workflow content."""

    def __init__(self):
        self.settings = get_settings()
        self.docling_max_bytes = self.settings.slide_markdown_docling_max_file_size_mb * 1024 * 1024
        self.fallback_max_bytes = (
            self.settings.slide_markdown_fallback_max_file_size_mb * 1024 * 1024
        )
        self.fallback_batch_pages = self.settings.slide_markdown_fallback_batch_pages
        self.fallback_max_pages = self.settings.slide_markdown_fallback_max_pages
        self.fallback_max_chars = self.settings.slide_markdown_fallback_max_chars

    @property
    def identifier(self) -> str:
        return "slide_markdown"

    @property
    def context_requirements(self) -> list[str]:
        # Slide deck is optional. This step no-ops when no deck is available.
        return []

    async def _generate(self, session_id: int, db, context: dict[str, Any]) -> dict[str, Any]:
        slide_payload = self._resolve_slide_payload(session_id, db, context)
        if slide_payload is None:
            return {
                "content": "",
                "content_type": "markdown",
                "persist": False,
                "meta_info": {"skipped": True, "reason": "slide_deck_missing"},
            }

        s3_key = slide_payload.get("s3_key")
        filename = slide_payload.get("filename") or "slides.pdf"
        declared_size = self._safe_int(slide_payload.get("size"))
        if not isinstance(s3_key, str) or not s3_key.strip():
            return {
                "content": "",
                "content_type": "markdown",
                "persist": False,
                "meta_info": {"skipped": True, "reason": "slide_deck_key_missing"},
            }

        if declared_size is not None and declared_size > self.fallback_max_bytes:
            return {
                "content": "",
                "content_type": "markdown",
                "persist": False,
                "meta_info": {
                    "skipped": True,
                    "reason": "slide_deck_too_large_for_extraction",
                    "declared_size": declared_size,
                    "max_supported_size": self.fallback_max_bytes,
                },
            }

        s3 = get_s3_slide_service()
        try:
            pdf_bytes = s3.download_slide(s3_key)
        except Exception as exc:
            logger.warning(
                "slide_markdown_download_failed",
                session_id=session_id,
                s3_key=s3_key,
                error=str(exc),
            )
            return {
                "content": "",
                "content_type": "markdown",
                "persist": False,
                "meta_info": {
                    "skipped": True,
                    "reason": "slide_deck_download_failed",
                },
            }

        file_size = len(pdf_bytes)
        if file_size > self.fallback_max_bytes:
            return {
                "content": "",
                "content_type": "markdown",
                "persist": False,
                "meta_info": {
                    "skipped": True,
                    "reason": "slide_deck_too_large_for_extraction",
                    "size": file_size,
                    "max_supported_size": self.fallback_max_bytes,
                },
            }

        conversion = None
        used_source = "pypdf"
        if file_size <= self.docling_max_bytes:
            docling = DoclingService()
            conversion = docling.convert_pdf_to_markdown(pdf_bytes=pdf_bytes, filename=filename)
            if conversion.get("success"):
                used_source = "docling"
            else:
                logger.warning(
                    "docling_conversion_failed_falling_back",
                    session_id=session_id,
                    s3_key=s3_key,
                    filename=filename,
                    size=file_size,
                    error=conversion.get("error"),
                )

        if conversion is None or not conversion.get("success"):
            pdf_text = PDFTextService()
            conversion = pdf_text.extract_markdown(
                pdf_bytes,
                batch_pages=self.fallback_batch_pages,
                max_pages=self.fallback_max_pages,
                max_chars=self.fallback_max_chars,
            )
            used_source = "pypdf"

        if not conversion.get("success"):
            logger.warning(
                "slide_markdown_extraction_failed",
                session_id=session_id,
                s3_key=s3_key,
                filename=filename,
                size=file_size,
                error=conversion.get("error"),
            )
            return {
                "content": "",
                "content_type": "markdown",
                "persist": False,
                "meta_info": {
                    "skipped": True,
                    "reason": "slide_markdown_extraction_failed",
                    "error": conversion.get("error"),
                    "size": file_size,
                },
            }

        markdown = conversion["markdown"]

        logger.info(
            "slide_markdown_generated",
            session_id=session_id,
            s3_key=s3_key,
            filename=filename,
            size=file_size,
            source=used_source,
            markdown_length=len(markdown),
        )

        return {
            "content": markdown,
            "content_type": "markdown",
            "meta_info": {
                "source": used_source,
                "filename": filename,
                "s3_key": s3_key,
                "size": file_size,
                "response_type": conversion.get("response_type"),
                "image_count": conversion.get("image_count", 0),
                "page_count": conversion.get("page_count"),
                "char_count": conversion.get("char_count"),
                "truncated": conversion.get("truncated", False),
            },
        }

    def _resolve_slide_payload(
        self, session_id: int, db, context: dict[str, Any]
    ) -> dict[str, Any] | None:
        raw = context.get("slide_deck")
        payload = self._parse_slide_payload(raw)
        if payload is not None:
            return payload

        existing = content_crud.get_content_by_identifier(db, session_id, "slide_deck")
        if not existing:
            return None

        return self._parse_slide_payload(existing.content)

    def _parse_slide_payload(self, raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            return raw

        if isinstance(raw, str):
            candidate = raw.strip()
            if candidate == "":
                return None
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed

        return None

    def _safe_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None


# Auto-register this step when imported
_slide_markdown_step = SlideMarkdownStep()
StepRegistry.register(_slide_markdown_step)
