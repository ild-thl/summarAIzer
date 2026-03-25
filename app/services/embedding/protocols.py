"""Protocol definitions for embedding domain components."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingsBackendProtocol(Protocol):
    """Required interface for embedding backends."""

    async def aembed_query(self, text: str) -> list[float]:
        """Asynchronously embed a query text."""


class ChromaCollectionProtocol(Protocol):
    """Subset of Chroma collection operations used by the service."""

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> Any: ...

    def query(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete(self, ids: list[str]) -> Any: ...

    def get(self, ids: list[str], include: list[str]) -> dict[str, Any]: ...


class ChromaClientProtocol(Protocol):
    """Subset of Chroma client operations used by the service."""

    def get_or_create_collection(
        self,
        name: str,
        metadata: dict[str, Any],
    ) -> ChromaCollectionProtocol: ...

    def get_collection(self, name: str) -> ChromaCollectionProtocol: ...


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Required vector-store operations used by embedding and recommendation services."""

    async def upsert_session(
        self,
        session_id: int,
        embedding: list[float],
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store or update a single session embedding."""

    async def query_similar_sessions(
        self,
        embedding: list[float],
        limit: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[int, float, str]]:
        """Retrieve similar sessions from vector storage."""

    async def delete_session(self, session_id: int) -> bool:
        """Delete a session embedding by its session ID."""

    async def get_session_embeddings(self, session_ids: list[int]) -> dict[int, list[float]]:
        """Fetch embeddings for the given session IDs."""
