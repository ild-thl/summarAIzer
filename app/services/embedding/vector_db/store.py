"""Chroma-backed vector store operations for session embeddings."""

from __future__ import annotations

import structlog

from app.services.embedding.protocols import ChromaCollectionProtocol

logger = structlog.get_logger()


class ChromaSessionVectorStore:
    """Encapsulates low-level Chroma CRUD/query operations for session vectors."""

    def __init__(self, sessions_collection: ChromaCollectionProtocol):
        self.sessions_collection = sessions_collection

    async def upsert_session(
        self,
        session_id: int,
        embedding: list[float],
        text: str,
        metadata: dict | None = None,
    ) -> None:
        try:
            chroma_metadata = {"session_id": session_id, "type": "session"}
            if metadata:
                chroma_metadata.update(metadata)

            self.sessions_collection.upsert(
                ids=[f"session_{session_id}"],
                embeddings=[embedding],
                documents=[text],
                metadatas=[chroma_metadata],
            )
            logger.debug(
                "session_embedding_stored",
                session_id=session_id,
                embedding_dimension=len(embedding),
                metadata_keys=list((metadata or {}).keys()),
            )
        except Exception as e:
            logger.error(
                "session_embedding_store_failed",
                session_id=session_id,
                error=str(e),
            )
            raise

    async def query_similar_sessions(
        self,
        embedding: list[float],
        limit: int = 10,
        where: dict | None = None,
    ) -> list[tuple[int, float, str]]:
        try:
            query_kwargs = {
                "query_embeddings": [embedding],
                "n_results": limit,
            }
            if where:
                query_kwargs["where"] = where

            results = self.sessions_collection.query(**query_kwargs)

            output: list[tuple[int, float, str]] = []
            if results["ids"] and len(results["ids"]) > 0:
                for i, chroma_id in enumerate(results["ids"][0]):
                    session_id = int(chroma_id.split("_")[1])
                    similarity = 1 - results["distances"][0][i]
                    text = results["documents"][0][i] if results["documents"] else ""
                    output.append((session_id, similarity, text))

            logger.debug(
                "session_search_complete",
                query_dimension=len(embedding),
                results_found=len(output),
                filters_applied=bool(where),
            )
            return output

        except Exception as e:
            logger.error(
                "session_search_failed",
                error=str(e),
            )
            raise

    async def delete_session(self, session_id: int) -> bool:
        try:
            chroma_id = f"session_{session_id}"
            self.sessions_collection.delete(ids=[chroma_id])
            logger.debug("session_embedding_deleted", session_id=session_id)
            return True
        except Exception as e:
            logger.error(
                "session_embedding_deletion_failed",
                session_id=session_id,
                error=str(e),
            )
            raise

    async def get_session_embeddings(self, session_ids: list[int]) -> dict[int, list[float]]:
        if not session_ids:
            return {}

        try:
            chroma_ids = [f"session_{sid}" for sid in session_ids]
            results = self.sessions_collection.get(ids=chroma_ids, include=["embeddings"])

            out: dict[int, list[float]] = {}
            if results["ids"]:
                for chroma_id, embedding in zip(
                    results["ids"], results["embeddings"], strict=False
                ):
                    session_id = int(chroma_id.split("_")[1])
                    out[session_id] = embedding

            logger.debug(
                "session_embeddings_retrieved",
                requested=len(session_ids),
                found=len(out),
            )
            return out

        except Exception as e:
            logger.error(
                "session_embeddings_retrieval_failed",
                error=str(e),
                session_ids_count=len(session_ids),
            )
            raise
