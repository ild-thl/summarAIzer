"""Short-lived Redis cache for query embeddings."""

from __future__ import annotations

import hashlib
import json

import structlog
from redis import asyncio as redis

logger = structlog.get_logger()


class EmbeddingQueryCache:
    """Caches query embeddings in Redis for a short time window."""

    def __init__(
        self,
        redis_url: str | None,
        ttl_seconds: int,
        redis_client: redis.Redis | None = None,
    ):
        self.ttl_seconds = max(0, ttl_seconds)
        self._client = redis_client

        if self._client is None and redis_url and self.ttl_seconds > 0:
            self._client = redis.from_url(redis_url, decode_responses=True)

    @property
    def enabled(self) -> bool:
        """Return whether the cache is active."""
        return self._client is not None and self.ttl_seconds > 0

    async def get(self, query_text: str) -> list[float] | None:
        """Return cached embedding for a normalized query, if available."""
        if not self.enabled:
            return None

        cache_key = self._build_cache_key(query_text)
        try:
            cached_value = await self._client.get(cache_key)
            if cached_value is None:
                return None

            embedding = self._deserialize_embedding(cached_value)
            if embedding is None:
                await self._client.delete(cache_key)
                return None

            await self._client.expire(cache_key, self.ttl_seconds)
            return embedding
        except Exception as exc:
            logger.warning("embedding_query_cache_get_failed", error=str(exc))
            return None

    async def set(self, query_text: str, embedding: list[float]) -> None:
        """Store an embedding for a normalized query."""
        if not self.enabled or not embedding:
            return

        cache_key = self._build_cache_key(query_text)
        try:
            await self._client.set(cache_key, json.dumps(embedding), ex=self.ttl_seconds)
        except Exception as exc:
            logger.warning("embedding_query_cache_set_failed", error=str(exc))

    @staticmethod
    def normalize_query_text(text: str) -> str:
        """Normalize query text for stable cache lookups."""
        return " ".join(text.split())

    def _build_cache_key(self, query_text: str) -> str:
        normalized_text = self.normalize_query_text(query_text)
        digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        return f"embedding_query:{digest}"

    @staticmethod
    def _deserialize_embedding(payload: str) -> list[float] | None:
        """Decode a cached embedding payload."""
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(value, list):
            return None

        if not all(isinstance(item, int | float) for item in value):
            return None

        return [float(item) for item in value]
