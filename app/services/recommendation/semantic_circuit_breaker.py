"""Redis-backed semantic circuit breaker for recommendation degradation."""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from redis import asyncio as redis

logger = structlog.get_logger()


class RecommendationSemanticCircuitBreaker:
    """Tracks semantic recommendation failures across processes using Redis."""

    def __init__(
        self,
        redis_url: str | None,
        failure_threshold: int,
        cooldown_minutes: int,
        redis_client: redis.Redis | None = None,
        key_prefix: str = "recommendation_semantic_circuit",
    ):
        self.failure_threshold = max(0, failure_threshold)
        self.cooldown_minutes = max(0, cooldown_minutes)
        self.cooldown_seconds = self.cooldown_minutes * 60
        self.key_prefix = key_prefix
        self._client = redis_client

        if (
            self._client is None
            and redis_url
            and self.failure_threshold > 0
            and self.cooldown_seconds > 0
        ):
            self._client = redis.from_url(redis_url, decode_responses=True)

    @property
    def enabled(self) -> bool:
        """Return whether the shared circuit breaker is active."""
        return self._client is not None and self.failure_threshold > 0 and self.cooldown_seconds > 0

    async def is_open(self) -> tuple[bool, datetime | None, int]:
        """Return whether the circuit is currently open plus metadata for logging."""
        if not self.enabled:
            return False, None, 0

        try:
            open_until_raw, failure_count_raw = await self._client.mget(
                self._open_until_key,
                self._failure_count_key,
            )
        except Exception as exc:
            logger.warning("recommendation_semantic_circuit_read_failed", error=str(exc))
            return False, None, 0

        open_until = self._parse_datetime(open_until_raw)
        failure_count = self._parse_int(failure_count_raw)
        if open_until is None:
            return False, None, failure_count

        now = datetime.utcnow()
        if now < open_until:
            return True, open_until, failure_count

        await self.reset()
        return False, None, 0

    async def record_success(self) -> None:
        """Clear failure state after a successful semantic request."""
        if not self.enabled:
            return

        await self.reset()

    async def record_failure(self, error_type: str) -> tuple[int, datetime | None]:
        """Record a semantic failure and open the circuit once threshold is reached."""
        if not self.enabled:
            return 0, None

        try:
            failure_count = await self._client.incr(self._failure_count_key)
            await self._client.expire(self._failure_count_key, self.cooldown_seconds)
        except Exception as exc:
            logger.warning(
                "recommendation_semantic_circuit_record_failure_failed",
                error=str(exc),
                error_type=error_type,
            )
            return 0, None

        if failure_count < self.failure_threshold:
            return int(failure_count), None

        open_until = datetime.utcnow() + timedelta(seconds=self.cooldown_seconds)
        try:
            await self._client.set(
                self._open_until_key, open_until.isoformat(), ex=self.cooldown_seconds
            )
        except Exception as exc:
            logger.warning(
                "recommendation_semantic_circuit_open_failed",
                error=str(exc),
                error_type=error_type,
                failure_count=int(failure_count),
            )
            return int(failure_count), None

        return int(failure_count), open_until

    async def reset(self) -> None:
        """Remove all persisted circuit breaker state."""
        if not self.enabled:
            return

        try:
            await self._client.delete(self._failure_count_key, self._open_until_key)
        except Exception as exc:
            logger.warning("recommendation_semantic_circuit_reset_failed", error=str(exc))

    @property
    def _failure_count_key(self) -> str:
        return f"{self.key_prefix}:failures"

    @property
    def _open_until_key(self) -> str:
        return f"{self.key_prefix}:open_until"

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None

        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_int(value: str | None) -> int:
        if value is None:
            return 0

        try:
            return int(value)
        except ValueError:
            return 0
