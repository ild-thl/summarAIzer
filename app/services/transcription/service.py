"""Transcription service — orchestrates providers in priority order."""

import structlog
from sqlalchemy.orm import Session as SQLSession

from app.services.transcription.exceptions import (
    TranscriptionPendingError,
    TranscriptionUnavailableError,
)
from app.services.transcription.whisper_provider import WhisperTranscriptionProvider
from app.services.transcription.youtube_provider import YouTubeTranscriptionProvider

logger = structlog.get_logger()

# Provider priority: YouTube first (free), Whisper second (costs compute)
_PROVIDERS = [
    YouTubeTranscriptionProvider(),
    WhisperTranscriptionProvider(),
]


def get_transcription(session_id: int, db: SQLSession, context: dict) -> str | None:
    """
    Try each provider in order and return the first successful transcription.

    Raises:
        TranscriptionPendingError: Immediately if any provider signals pending
            audio (blocking error — caller must fail the workflow).

    Returns:
        Transcription text, or None if no provider could supply one
        (non-blocking; callers should log and continue).
    """
    for provider in _PROVIDERS:
        try:
            if not provider.can_handle(session_id, db, context):
                continue
        except TranscriptionPendingError:
            # Re-raise — this is blocking
            raise

        try:
            text = provider.transcribe(session_id, db, context)
            logger.info(
                "transcription_fetched",
                session_id=session_id,
                provider=type(provider).__name__,
                char_count=len(text),
            )
            return text
        except TranscriptionUnavailableError as exc:
            logger.warning(
                "transcription_provider_unavailable",
                session_id=session_id,
                provider=type(provider).__name__,
                reason=str(exc),
            )
            # Try next provider

    return None
