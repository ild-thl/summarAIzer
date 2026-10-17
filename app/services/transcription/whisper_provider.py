"""Whisper API transcription provider — uses processed FLAC chunks from S3."""

from urllib.parse import urlsplit, urlunsplit

import structlog
from openai import APIError, OpenAI
from sqlalchemy.orm import Session as SQLSession

from app.config.settings import get_settings
from app.database.models import AudioFileProcessingStatus
from app.services.provider_request_control import (
    DEFAULT_MAX_RETRIES,
    acquire_provider_request_slot,
)
from app.services.s3_audio_service import get_s3_audio_service
from app.services.transcription.exceptions import (
    TranscriptionPendingError,
    TranscriptionRequestError,
    TranscriptionUnavailableError,
)

logger = structlog.get_logger()


class WhisperTranscriptionProvider:
    """
    Transcribes audio using an OpenAI-compatible Whisper endpoint.

    Audio chunks are downloaded from S3 (processed FLAC) and sent to
    the configured transcription endpoint one by one. The results are
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

        parts: list[str] = []
        endpoint_url = settings.transcription_api_url
        base_url = _transcription_base_url(endpoint_url)
        client = OpenAI(
            base_url=base_url,
            api_key=settings.transcription_api_key,
            timeout=300,
            max_retries=DEFAULT_MAX_RETRIES,
        )
        try:
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
                        client=client,
                        endpoint_url=endpoint_url,
                        chunk_bytes=chunk_bytes,
                        settings=settings,
                        session_id=session_id,
                        audio_file_id=audio_file.id,
                        chunk_key=chunk_key,
                    )
                    parts.append(chunk_text)
        finally:
            client.close()

        return " ".join(parts).strip()

    # ------------------------------------------------------------------

    @staticmethod
    def _call_whisper(
        client: OpenAI,
        endpoint_url: str,
        chunk_bytes: bytes,
        settings,
        session_id: int,
        audio_file_id: int,
        chunk_key: str,
    ) -> str:
        """Send a single FLAC chunk to the Whisper endpoint and return text."""
        acquire_provider_request_slot()
        try:
            result = client.audio.transcriptions.create(
                model=settings.transcription_model,
                file=("chunk.flac", chunk_bytes, "audio/flac"),
                response_format=settings.transcription_response_format,
                temperature=settings.openai_transcribe_temperature,
            )
        except APIError as exc:
            response = getattr(exc, "response", None)
            response_body = getattr(response, "text", "") if response is not None else ""
            status_code = getattr(exc, "status_code", None)
            request_id = getattr(exc, "request_id", None)
            endpoint = _safe_endpoint(endpoint_url)
            logger.error(
                "whisper_transcription_request_failed",
                endpoint=endpoint,
                session_id=session_id,
                audio_file_id=audio_file_id,
                chunk_key=chunk_key,
                chunk_size_bytes=len(chunk_bytes),
                file_signature=chunk_bytes[:4].decode("ascii", errors="replace"),
                model=settings.transcription_model,
                response_format=settings.transcription_response_format,
                status_code=status_code,
                request_id=request_id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                provider_response=response_body[:1000] or None,
                provider_response_truncated=len(response_body) > 1000,
            )
            status = status_code if status_code is not None else "connection_error"
            request_suffix = f", request_id={request_id}" if request_id else ""
            raise TranscriptionRequestError(
                f"Whisper request failed (status={status}{request_suffix})"
            ) from None

        if isinstance(result, str):
            return result.strip()
        return (result.text or "").strip()


def _transcription_base_url(endpoint_url: str) -> str:
    """Convert a configured full transcription endpoint to the SDK base URL."""
    endpoint_url = endpoint_url.rstrip("/")
    suffix = "/audio/transcriptions"
    if endpoint_url.endswith(suffix):
        return endpoint_url[: -len(suffix)]
    return endpoint_url


def _safe_endpoint(endpoint_url: str) -> str:
    """Return endpoint scheme, host, and path without credentials or query values."""
    parsed = urlsplit(endpoint_url)
    authority = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
