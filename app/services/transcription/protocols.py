"""Protocol / interface for transcription providers."""

from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session as SQLSession


@runtime_checkable
class TranscriptionProvider(Protocol):
    """Interface all transcription providers must implement."""

    def can_handle(self, session_id: int, db: SQLSession, context: dict) -> bool:
        """
        Return True if this provider can supply a transcription for the session.

        Raises:
            TranscriptionPendingError: If audio files exist but are still processing.
        """
        ...

    def transcribe(self, session_id: int, db: SQLSession, context: dict) -> str:
        """
        Return transcription text for the session.

        Returns:
            Plain-text transcription.

        Raises:
            TranscriptionError: On any failure to produce a transcription.
        """
        ...
