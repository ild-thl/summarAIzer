"""Tests for WhisperTranscriptionProvider status handling."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.database.models import AudioFileProcessingStatus
from app.services.transcription.exceptions import TranscriptionPendingError
from app.services.transcription.whisper_provider import WhisperTranscriptionProvider


def test_can_handle_returns_true_for_mixed_processed_and_pending_files():
    """A processed file is enough to proceed even if another file is still pending."""
    provider = WhisperTranscriptionProvider()
    db = Mock()

    audio_files = [
        SimpleNamespace(processing_status=AudioFileProcessingStatus.PENDING),
        SimpleNamespace(processing_status=AudioFileProcessingStatus.PROCESSED),
    ]

    with patch(
        "app.crud.audio_file.get_audio_files_for_session",
        return_value=audio_files,
    ):
        assert provider.can_handle(session_id=32, db=db, context={}) is True


def test_can_handle_raises_when_only_pending_files_exist():
    """Pending files remain blocking until at least one processed file exists."""
    provider = WhisperTranscriptionProvider()
    db = Mock()

    audio_files = [
        SimpleNamespace(processing_status=AudioFileProcessingStatus.PENDING),
        SimpleNamespace(processing_status=AudioFileProcessingStatus.PROCESSING),
    ]

    with (
        patch(
            "app.crud.audio_file.get_audio_files_for_session",
            return_value=audio_files,
        ),
        pytest.raises(TranscriptionPendingError, match="still processing"),
    ):
        provider.can_handle(session_id=32, db=db, context={})
