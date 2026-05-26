"""Tests for provider request throttling and retry behavior."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.image_generation_service import ImageGenerationService
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


def test_whisper_retries_after_rate_limit_then_returns_text():
    """Whisper calls should retry 429s instead of failing the whole workflow immediately."""
    settings = SimpleNamespace(
        transcription_model="whisper-large-v2",
        transcription_response_format="text",
        openai_transcribe_temperature=0.4,
    )

    rate_limited_response = Mock(status_code=429, headers={"Retry-After": "0"})
    rate_limited_response.raise_for_status.side_effect = AssertionError(
        "raise_for_status should only run on the final response"
    )

    success_response = Mock(status_code=200, headers={}, text=" transcribed text ")
    success_response.raise_for_status.return_value = None

    with (
        patch("app.services.provider_request_control.DEFAULT_RATE_LIMITER.acquire"),
        patch("app.services.provider_request_control.time.sleep") as sleep_mock,
        patch(
            "app.services.transcription.whisper_provider.requests.post",
            side_effect=[rate_limited_response, success_response],
        ) as post_mock,
    ):
        result = WhisperTranscriptionProvider._call_whisper(
            url="https://example.test/audio/transcriptions",
            headers={"Authorization": "Bearer test-key"},
            chunk_bytes=b"fake-flac-bytes",
            settings=settings,
        )

    assert result == "transcribed text"
    assert post_mock.call_count == 2
    sleep_mock.assert_called_once_with(0.0)
