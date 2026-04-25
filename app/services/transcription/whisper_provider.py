"""Whisper API transcription provider — uses processed FLAC chunks from S3."""

import io

import requests
import structlog
from sqlalchemy.orm import Session as SQLSession

from app.config.settings import get_settings
from app.database.models import AudioFileProcessingStatus
from app.services.s3_audio_service import get_s3_audio_service
from app.services.transcription.exceptions import (
    TranscriptionPendingError,
    TranscriptionUnavailableError,
)

logger = structlog.get_logger()


class WhisperTranscriptionProvider:
    """
    Transcribes audio using an OpenAI-compatible Whisper endpoint.

    Audio chunks are downloaded from S3 (processed FLAC) and sent to
    `{LLM_BASE_URL}/audio/transcriptions` one by one. The results are
    concatenated in file_order → chunk index order.
    """

    def can_handle(self, session_id: int, db: SQLSession, context: dict) -> bool:
        """
        Return True if at least one PROCESSED audio file exists for the session.

        Raises:
            TranscriptionPendingError: If files are still pending and none are processed yet.
        """
        from app.crud.audio_file import get_audio_files_for_session

        # Use serialised dicts from context if available (avoids extra DB query in workflow)
        audio_files = context.get("audio_files")
        if audio_files is None:
            audio_files = [
                {"processing_status": af.processing_status.value}
                for af in get_audio_files_for_session(db, session_id)
            ]

        statuses = [
            (
                status.value
                if isinstance(status := af["processing_status"], AudioFileProcessingStatus)
                else status
            )
            for af in audio_files
        ]

        if AudioFileProcessingStatus.PROCESSED.value in statuses:
            return True

        pending = [s for s in statuses if s in ("pending", "processing")]
        if pending:
            raise TranscriptionPendingError(
                f"Session {session_id} has {len(pending)} audio file(s) still processing. "
                "Retry after they complete."
            )

        return False

    def transcribe(self, session_id: int, db: SQLSession, context: dict) -> str:  # noqa: ARG002
        """
        Download all FLAC chunks for the session and transcribe via Whisper.

        Files are processed in file_order order; chunks within each file are
        processed in chunk index order (guaranteed by S3 key sorting in S3AudioService).

        Returns:
            Concatenated plain-text transcription.
        """
        from app.crud.audio_file import get_audio_files_for_session

        settings = get_settings()
        s3 = get_s3_audio_service()

        audio_files = get_audio_files_for_session(db, session_id)
        processed = [
            af
            for af in audio_files
            if af.processing_status == AudioFileProcessingStatus.PROCESSED and af.s3_prefix
        ]

        if not processed:
            raise TranscriptionUnavailableError(
                f"No processed audio files found for session {session_id}"
            )

        url = f"{settings.llm_base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

        parts: list[str] = []
        for audio_file in sorted(processed, key=lambda af: af.file_order):
            chunk_keys = s3.list_chunk_keys(session_id, audio_file.id)
            logger.info(
                "whisper_transcribing_file",
                session_id=session_id,
                audio_file_id=audio_file.id,
                file_order=audio_file.file_order,
                chunk_count=len(chunk_keys),
            )
            for chunk_key in chunk_keys:
                chunk_bytes = s3.download_chunk(chunk_key)
                chunk_text = self._call_whisper(
                    url=url,
                    headers=headers,
                    chunk_bytes=chunk_bytes,
                    settings=settings,
                )
                parts.append(chunk_text)

        return " ".join(parts).strip()

    # ------------------------------------------------------------------

    @staticmethod
    def _call_whisper(url: str, headers: dict, chunk_bytes: bytes, settings) -> str:
        """Send a single FLAC chunk to the Whisper endpoint and return text."""
        response = requests.post(
            url,
            headers=headers,
            files={"file": ("chunk.flac", io.BytesIO(chunk_bytes), "audio/flac")},
            data={
                "model": settings.transcription_model,
                "response_format": settings.transcription_response_format,
                "temperature": str(settings.openai_transcribe_temperature),
            },
            timeout=300,  # 5 min per chunk
        )
        response.raise_for_status()

        if settings.transcription_response_format == "text":
            return response.text.strip()

        # json / verbose_json
        payload = response.json()
        return (payload.get("text") or "").strip()
