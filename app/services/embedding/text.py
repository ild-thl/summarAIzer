"""Embedding text preparation and validation utilities."""

from __future__ import annotations

from typing import Any


class EmbeddingTextHelper:
    """Utility methods for text validation and canonicalization."""

    @staticmethod
    def validate_embedding_text(text: str | None) -> bool:
        if not text:
            return False
        return bool(text.strip())

    @staticmethod
    def prepare_text(
        data: dict[str, Any],
        title_key: str,
        description_key: str,
        default_description: str,
        speakers_key: str | None = None,
        fallback_speakers: str = "No speaker listed",
    ) -> str:
        title = data.get(title_key, "")
        description = data.get(description_key, default_description)

        if speakers_key:
            speakers = data.get(speakers_key, fallback_speakers)
            return f"{title}. {description}. Speakers: {speakers}."

        return f"{title}. {description}."

    @staticmethod
    def prepare_session_text_with_summary(
        session_data: dict[str, Any],
        summary_data: dict[str, Any] | None,
    ) -> str:
        title = session_data.get("title", "")
        description = session_data.get("description", "No description available")

        if summary_data and summary_data.get("summary"):
            summary = summary_data["summary"]
            return f"{title}. {description}. Summary: {summary}."

        speakers = session_data.get("speakers", "No speaker listed")
        return f"{title}. {description}. Speakers: {speakers}."
