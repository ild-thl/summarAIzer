"""Service wrapper for Docling document conversion API."""

import logging
from typing import Any

import requests

from app.config.settings import get_settings
from app.services.provider_request_control import perform_rate_limited_request

logger = logging.getLogger(__name__)


class DoclingService:
    """Converts PDF files to markdown via Academic Cloud Docling API."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ):
        settings = get_settings()
        self.api_url = api_url or settings.docling_api_url
        self.api_key = api_key if api_key is not None else settings.docling_api_key
        self.timeout_seconds = timeout_seconds or settings.docling_request_timeout_seconds
        self.max_retries = settings.docling_max_retries if max_retries is None else max_retries

    def _build_headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("No API key configured for Docling. Set DOCLING_API_KEY.")

        return {
            "accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def convert_pdf_to_markdown(self, pdf_bytes: bytes, filename: str) -> dict[str, Any]:
        """Convert PDF bytes to markdown using Docling."""
        if not pdf_bytes:
            return {"success": False, "error": "PDF input is empty"}

        headers = self._build_headers()

        files = {
            "document": (filename or "slides.pdf", pdf_bytes, "application/pdf"),
        }

        try:
            response = perform_rate_limited_request(
                lambda: requests.post(
                    self.api_url,
                    params={"response_type": "markdown"},
                    headers=headers,
                    files=files,
                    timeout=self.timeout_seconds,
                ),
                operation_name="docling_pdf_convert",
                max_retries=self.max_retries,
            )
        except requests.RequestException as exc:
            logger.error("docling_request_failed", error=str(exc), exc_info=True)
            return {"success": False, "error": f"Docling request failed: {exc!s}"}

        if response.status_code != 200:
            error_text = response.text[:1000] if response.text else "No response body"
            logger.error(
                "docling_request_non_200",
                error=f"Status {response.status_code}: {error_text}",
                exc_info=True,
            )
            return {
                "success": False,
                "error": f"Docling API error {response.status_code}: {error_text}",
            }

        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("docling_response_not_json", error=str(exc), exc_info=True)
            return {"success": False, "error": "Docling response was not valid JSON"}

        markdown = payload.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return {"success": False, "error": "Docling response did not contain markdown"}

        return {
            "success": True,
            "markdown": markdown,
            "response_type": payload.get("response_type"),
            "filename": payload.get("filename") or filename,
            "image_count": len(payload.get("images") or []),
        }
