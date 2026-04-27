"""Transcription service exceptions."""


class TranscriptionError(Exception):
    """Base class for transcription errors."""


class TranscriptionUnavailableError(TranscriptionError):
    """
    Raised when no transcription source is available for a session.

    This is non-blocking: callers may log and continue without a transcription.
    """


class TranscriptionPendingError(TranscriptionError):
    """
    Raised when audio files exist but are not yet processed (pending/processing).

    This is blocking: the workflow should fail immediately so it can be
    re-triggered once processing is complete.
    """
