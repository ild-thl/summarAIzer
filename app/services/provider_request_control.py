"""Shared throttling and retry helpers for outbound provider HTTP requests."""

import time
from collections.abc import Callable

import requests
import structlog
from langchain_core.rate_limiters import InMemoryRateLimiter

logger = structlog.get_logger()

# Shared across chat, image generation, and Whisper calls within a worker process.
DEFAULT_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=1,
    check_every_n_seconds=0.1,
    max_bucket_size=1,
)

RETRYABLE_STATUS_CODES = {429, 503}
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_DELAY_SECONDS = 8.0


def acquire_provider_request_slot() -> None:
    """Block until the shared provider rate limiter allows another request."""
    DEFAULT_RATE_LIMITER.acquire(blocking=True)


def perform_rate_limited_request(
    send: Callable[[], requests.Response],
    *,
    operation_name: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> requests.Response:
    """Execute a provider HTTP request with shared throttling and bounded retries."""
    attempt = 0

    while True:
        acquire_provider_request_slot()

        try:
            response = send()
        except RETRYABLE_EXCEPTIONS as exc:
            if attempt >= max_retries:
                raise

            delay_seconds = _retry_delay_seconds(attempt=attempt)
            logger.info(
                "provider_request_exception_retrying operation=%s attempt=%s/%s delay=%.2fs error_type=%s error=%s",
                operation_name,
                attempt + 1,
                max_retries + 1,
                delay_seconds,
                type(exc).__name__,
                exc,
            )
            time.sleep(delay_seconds)
            attempt += 1
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= max_retries:
            return response

        delay_seconds = _retry_delay_seconds(
            attempt=attempt,
            retry_after_header=response.headers.get("Retry-After"),
        )
        logger.info(
            "provider_request_retrying operation=%s attempt=%s/%s delay=%.2fs status=%s",
            operation_name,
            attempt + 1,
            max_retries + 1,
            delay_seconds,
            response.status_code,
        )
        time.sleep(delay_seconds)
        attempt += 1


def _retry_delay_seconds(attempt: int, retry_after_header: str | None = None) -> float:
    """Return retry delay, honoring numeric Retry-After when available."""
    retry_after_seconds = _parse_retry_after_seconds(retry_after_header)
    if retry_after_seconds is not None:
        return retry_after_seconds

    return min(float(2**attempt), MAX_RETRY_DELAY_SECONDS)


def _parse_retry_after_seconds(retry_after_header: str | None) -> float | None:
    """Parse numeric Retry-After values in seconds."""
    if retry_after_header is None:
        return None

    try:
        retry_after_seconds = float(retry_after_header.strip())
    except ValueError:
        return None

    return max(0.0, retry_after_seconds)
