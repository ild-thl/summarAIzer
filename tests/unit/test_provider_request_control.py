"""Tests for provider request throttling and retry behavior."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from openai import APIStatusError

from app.database.models import AudioFileProcessingStatus
from app.services.image_generation_service import ImageGenerationService
from app.services.transcription.exceptions import TranscriptionRequestError
from app.services.transcription.whisper_provider import WhisperTranscriptionProvider


def test_image_generation_retries_after_rate_limit_then_succeeds():
    """Image generation should retry local 429s before surfacing failure."""
    service = ImageGenerationService(api_key="test-key")

    rate_limited_response = Mock(status_code=429, headers={"Retry-After": "0"})
    rate_limited_response.json.return_value = {"error": {"message": "API rate limit exceeded"}}

    success_response = Mock(status_code=200, headers={})
    success_response.json.return_value = {"data": [{"b64_json": "ZmFrZQ=="}]}

    with (
        patch("app.services.provider_request_control.DEFAULT_RATE_LIMITER.acquire"),
        patch("app.services.provider_request_control.time.sleep") as sleep_mock,
        patch(
            "app.services.image_generation_service.requests.post",
            side_effect=[rate_limited_response, success_response],
        ) as post_mock,
    ):
        result = service.generate_image(prompt="Festival collage")

    assert result == {"success": True, "images": [{"b64_json": "ZmFrZQ=="}]}
    assert post_mock.call_count == 2
    sleep_mock.assert_called_once_with(0.0)


def test_whisper_uses_transcription_specific_url_and_api_key(test_db):
    """Whisper should route requests using transcription-specific settings."""
    audio_file = SimpleNamespace(
        id=42,
        file_order=0,
        s3_prefix="session/42",
        processing_status=AudioFileProcessingStatus.PROCESSED,
    )
    settings = SimpleNamespace(
        transcription_api_url="https://transcribe.example.test/v1/audio/transcriptions",
        transcription_api_key="transcription-key",
        transcription_model="whisper-3-large",
        transcription_response_format="json",
        openai_transcribe_temperature=0.4,
    )
    provider = WhisperTranscriptionProvider()
    client = Mock()
    client.audio.transcriptions.create.side_effect = [
        SimpleNamespace(text="first chunk"),
        SimpleNamespace(text="second chunk"),
    ]

    with (
        patch("app.crud.audio_file.get_audio_files_for_session", return_value=[audio_file]),
        patch("app.services.transcription.whisper_provider.get_settings", return_value=settings),
        patch(
            "app.services.transcription.whisper_provider.OpenAI", return_value=client
        ) as openai_client,
        patch("app.services.transcription.whisper_provider.acquire_provider_request_slot"),
        patch("app.services.transcription.whisper_provider.get_s3_audio_service") as get_s3,
    ):
        get_s3.return_value.list_chunk_keys.return_value = [
            "chunk_0000.flac",
            "chunk_0001.flac",
        ]
        get_s3.return_value.download_chunk.side_effect = [b"fLaCfirst", b"fLaCsecond"]
        result = provider.transcribe(1, test_db, {})

    assert result == "first chunk second chunk"
    openai_client.assert_called_once_with(
        base_url="https://transcribe.example.test/v1",
        api_key="transcription-key",
        timeout=300,
        max_retries=2,
    )
    assert client.audio.transcriptions.create.call_args_list[0].kwargs == {
        "model": "whisper-3-large",
        "file": ("chunk.flac", b"fLaCfirst", "audio/flac"),
        "response_format": "json",
        "temperature": 0.4,
    }
    assert client.audio.transcriptions.create.call_args_list[1].kwargs["file"] == (
        "chunk.flac",
        b"fLaCsecond",
        "audio/flac",
    )
    client.close.assert_called_once()


def test_whisper_returns_text_response_from_openai_client():
    """The OpenAI client may return plain text when text response format is requested."""
    settings = SimpleNamespace(
        transcription_model="whisper-large-v2",
        transcription_response_format="text",
        openai_transcribe_temperature=0.4,
    )
    client = Mock()
    client.audio.transcriptions.create.return_value = " transcribed text "

    with (
        patch("app.services.transcription.whisper_provider.acquire_provider_request_slot") as slot,
    ):
        result = WhisperTranscriptionProvider._call_whisper(
            client=client,
            endpoint_url="https://example.test/v1/audio/transcriptions",
            chunk_bytes=b"fLaCfake-flac-bytes",
            settings=settings,
            session_id=10,
            audio_file_id=20,
            chunk_key="chunk_0000.flac",
        )

    assert result == "transcribed text"
    slot.assert_called_once_with()


def test_whisper_logs_provider_response_without_reraising_sdk_traceback():
    """Provider errors retain useful response details but expose a concise app error."""
    settings = SimpleNamespace(
        transcription_model="whisper-3-large",
        transcription_response_format="json",
        openai_transcribe_temperature=0.4,
    )
    request = httpx.Request("POST", "https://example.test/v1/audio/transcriptions")
    response = httpx.Response(
        401,
        request=request,
        text='{"error":"invalid credential"}',
    )
    client = Mock()
    client.audio.transcriptions.create.side_effect = APIStatusError(
        "Unauthorized", response=response, body={"error": "invalid credential"}
    )

    with (
        patch("app.services.transcription.whisper_provider.acquire_provider_request_slot"),
        patch("app.services.transcription.whisper_provider.logger.error") as log_error,
        pytest.raises(TranscriptionRequestError, match="status=401") as exc_info,
    ):
        WhisperTranscriptionProvider._call_whisper(
            client=client,
            endpoint_url="https://example.test/v1/audio/transcriptions?token=hidden",
            chunk_bytes=b"fLaCfake-flac-bytes",
            settings=settings,
            session_id=10,
            audio_file_id=20,
            chunk_key="chunk_0000.flac",
        )

    assert exc_info.value.__suppress_context__ is True
    assert log_error.call_args.kwargs["status_code"] == 401
    assert log_error.call_args.kwargs["request_id"] is None
    assert log_error.call_args.kwargs["provider_response"] == '{"error":"invalid credential"}'
    assert log_error.call_args.kwargs["endpoint"] == (
        "https://example.test/v1/audio/transcriptions"
    )
    assert log_error.call_args.kwargs["session_id"] == 10
    assert log_error.call_args.kwargs["audio_file_id"] == 20
    assert log_error.call_args.kwargs["chunk_key"] == "chunk_0000.flac"
    assert log_error.call_args.kwargs["chunk_size_bytes"] == len(b"fLaCfake-flac-bytes")
    assert log_error.call_args.kwargs["file_signature"] == "fLaC"
