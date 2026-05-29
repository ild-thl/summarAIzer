"""Local PDF text extraction service for large slide decks."""

from io import BytesIO

import structlog
from pypdf import PdfReader

logger = structlog.get_logger()


class PDFTextService:
    """Extracts text from PDFs and returns markdown."""

    @staticmethod
    def _load_reader(pdf_bytes: bytes) -> tuple[PdfReader | None, str | None]:
        """Load PDF reader from bytes and return optional error."""
        try:
            return PdfReader(BytesIO(pdf_bytes), strict=False), None
        except Exception as exc:
            return None, f"Failed to read PDF: {exc!s}"

    @staticmethod
    def _safe_extract_page_text(reader: PdfReader, index: int) -> str:
        """Extract text from a single page and handle extraction failures."""
        try:
            return reader.pages[index].extract_text() or ""
        except Exception as exc:
            logger.warning(
                "pdf_page_extract_failed",
                page_number=index + 1,
                error=str(exc),
            )
            return ""

    @staticmethod
    def _append_segment_with_limit(
        parts: list[str],
        segment: str,
        collected_chars: int,
        max_chars: int,
    ) -> tuple[int, bool]:
        """Append segment while respecting max_chars and report truncation."""
        if collected_chars + len(segment) > max_chars:
            remaining = max_chars - collected_chars
            if remaining > 0:
                parts.append(segment[:remaining])
            return collected_chars, True

        parts.append(segment)
        return collected_chars + len(segment), False

    def _collect_markdown_parts(
        self,
        reader: PdfReader,
        total_pages: int,
        *,
        batch_pages: int,
        max_chars: int,
    ) -> tuple[list[str], bool]:
        """Collect markdown parts from all pages with batching and char-limit truncation."""
        batch_size = max(1, batch_pages)
        collected_chars = 0
        parts: list[str] = []
        truncated = False

        for start in range(0, total_pages, batch_size):
            end = min(start + batch_size, total_pages)
            for idx in range(start, end):
                page_text = self._safe_extract_page_text(reader, idx)
                normalized = page_text.strip()
                if not normalized:
                    continue

                segment = f"### Page {idx + 1}\n\n{normalized}\n"
                collected_chars, truncated = self._append_segment_with_limit(
                    parts,
                    segment,
                    collected_chars,
                    max_chars,
                )
                if truncated:
                    return parts, True

        return parts, False

    def extract_markdown(
        self,
        pdf_bytes: bytes,
        *,
        batch_pages: int = 25,
        max_pages: int = 400,
        max_chars: int = 120000,
    ) -> dict:
        """Extract markdown from PDF bytes with memory-safe limits."""
        if not pdf_bytes:
            return {"success": False, "error": "PDF input is empty"}

        reader, read_error = self._load_reader(pdf_bytes)
        if read_error:
            return {"success": False, "error": read_error}
        if reader is None:
            return {"success": False, "error": "Failed to read PDF"}

        total_pages = len(reader.pages)
        if total_pages == 0:
            return {"success": False, "error": "PDF has no pages"}

        if total_pages > max_pages:
            return {
                "success": False,
                "error": f"PDF has {total_pages} pages, exceeds max_pages={max_pages}",
                "page_count": total_pages,
            }

        parts, truncated = self._collect_markdown_parts(
            reader,
            total_pages,
            batch_pages=batch_pages,
            max_chars=max_chars,
        )

        markdown = "\n".join(parts).strip()
        if not markdown:
            return {"success": False, "error": "No extractable text found in PDF"}

        if truncated:
            markdown += (
                "\n\n> Note: Extraction was truncated due to max character limit " f"({max_chars})."
            )

        return {
            "success": True,
            "markdown": markdown,
            "page_count": total_pages,
            "char_count": len(markdown),
            "truncated": truncated,
        }
