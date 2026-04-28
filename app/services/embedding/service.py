"""Embedding service for semantic search using configurable embeddings and Chroma storage."""

import structlog

from app.constants.embedding import (
    MAX_EMBEDDING_TEXT_LENGTH,
    SESSIONS_COLLECTION,
)
from app.crud import generated_content as content_crud
from app.database.connection import SessionLocal
from app.services.embedding.metadata import EmbeddingMetadataBuilder
from app.services.embedding.protocols import (
    ChromaClientProtocol,
    ChromaCollectionProtocol,
    EmbeddingsBackendProtocol,
    VectorStoreProtocol,
)
from app.services.embedding.providers.factory import create_embeddings_backend
from app.services.embedding.query_cache import EmbeddingQueryCache
from app.services.embedding.text import EmbeddingTextHelper
from app.services.embedding.vector_db.chroma import ChromaInitializer
from app.services.embedding.vector_db.store import ChromaSessionVectorStore

logger = structlog.get_logger()


class EmbeddingService:
    """Service for generating embeddings and storing/searching vectors in Chroma."""

    def __init__(
        self,
        embedding_provider: str = "huggingface",
        embedding_api_key: str | None = None,
        embedding_api_base_url: str | None = None,
        embedding_model_name: str | None = None,
        embedding_request_timeout_seconds: float = 3.0,
        chroma_url: str = "http://localhost:8000",
        chroma_tenant: str = "default_tenant",
        chroma_credentials: str | None = None,
        chroma_provider: str | None = None,
        embedding_dimension: int = 768,
        embedding_query_cache_url: str | None = None,
        embedding_query_cache_ttl_seconds: int = 600,
        embedding_query_cache: EmbeddingQueryCache | None = None,
    ):
        """
        Initialize embedding service with configurable embeddings backend and Chroma vector storage.

        Args:
            embedding_provider: "openai" or "huggingface"
            embedding_api_key: API key for the embedding service
            embedding_api_base_url: Base URL for the embedding API
            embedding_model_name: Model name (used for OpenAI provider)
            chroma_url: Chroma server host
            chroma_tenant: Chroma tenant name
            chroma_credentials: Optional Chroma authentication token
            chroma_provider: Optional Chroma authentication provider
            embedding_dimension: Expected embedding dimension (for validation)
        """
        self.provider = embedding_provider
        self.embedding_dimension = embedding_dimension
        self.text_helper = EmbeddingTextHelper()
        self.metadata_builder = EmbeddingMetadataBuilder()
        self.query_cache = embedding_query_cache or EmbeddingQueryCache(
            redis_url=embedding_query_cache_url,
            ttl_seconds=embedding_query_cache_ttl_seconds,
        )

        try:
            # Initialize embeddings backend based on provider
            if embedding_provider == "openai":
                if not all([embedding_api_key, embedding_api_base_url, embedding_model_name]):
                    raise ValueError(
                        "OpenAI embeddings requires: embedding_api_key, embedding_api_base_url, embedding_model_name"
                    )
                self.embeddings: EmbeddingsBackendProtocol = create_embeddings_backend(
                    provider="openai",
                    api_key=embedding_api_key,
                    api_base_url=embedding_api_base_url,
                    model=embedding_model_name,
                )
                logger.info(
                    "embedding_backend_initialized",
                    provider="openai",
                    model=embedding_model_name,
                )
            elif embedding_provider == "huggingface":
                if not all([embedding_api_key, embedding_api_base_url]):
                    raise ValueError(
                        "HuggingFace embeddings requires: embedding_api_key, embedding_api_base_url"
                    )
                self.embeddings = create_embeddings_backend(
                    provider="huggingface",
                    api_key=embedding_api_key,
                    api_base_url=embedding_api_base_url,
                    request_timeout_seconds=embedding_request_timeout_seconds,
                )
                logger.info(
                    "embedding_backend_initialized",
                    provider="huggingface",
                    api_base_url=embedding_api_base_url,
                    request_timeout_seconds=embedding_request_timeout_seconds,
                )
            else:
                raise ValueError(
                    f"Unknown embedding provider: {embedding_provider}. Use 'openai' or 'huggingface'"
                )

            # Initialize Chroma client
            self.chroma_client: ChromaClientProtocol = ChromaInitializer.create_client(
                chroma_url=chroma_url,
                chroma_tenant=chroma_tenant,
                chroma_credentials=chroma_credentials,
                chroma_provider=chroma_provider,
            )

            # Get or create collections with explicit configuration
            self.sessions_collection: ChromaCollectionProtocol = self._init_collection(
                SESSIONS_COLLECTION
            )
            self.vector_store: VectorStoreProtocol = ChromaSessionVectorStore(
                self.sessions_collection
            )
            logger.info("embedding_collections_ready", sessions=True)

        except Exception as e:
            logger.error(
                "embedding_service_initialization_failed",
                error=str(e),
                provider=embedding_provider,
                chroma_url=chroma_url,
            )
            raise

    def _init_collection(self, name: str) -> ChromaCollectionProtocol:
        """
        Initialize a Chroma collection with fallback pattern.

        Tries to get or create with metadata, falls back to getting existing collection.

        Args:
            name: Collection name

        Returns:
            Initialized Chroma collection

        Raises:
            Exception: If collection cannot be initialized
        """
        return ChromaInitializer.init_collection(self.chroma_client, name)

    async def embed_query(self, text: str) -> list[float]:
        """
        Generate embedding for a query/document text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats

        Raises:
            Exception: If embedding fails
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        normalized_text = EmbeddingQueryCache.normalize_query_text(text)

        cached_embedding = await self._get_cached_query_embedding(normalized_text)
        if cached_embedding is not None:
            logger.debug(
                "embedding_query_cache_hit",
                text_length=len(normalized_text),
                provider=self.provider,
            )
            return cached_embedding

        try:
            logger.debug(
                "embedding_query_start",
                text_length=len(normalized_text),
                provider=self.provider,
            )

            # Use async embedding
            embedding = await self.embeddings.aembed_query(normalized_text)

            # Validate embedding dimension
            if len(embedding) != self.embedding_dimension:
                logger.warning(
                    "embedding_dimension_mismatch",
                    expected=self.embedding_dimension,
                    actual=len(embedding),
                    provider=self.provider,
                )

            logger.debug(
                "embedding_query_complete",
                embedding_dimension=len(embedding),
                provider=self.provider,
            )

            await self._cache_query_embedding(normalized_text, embedding)

            return embedding

        except Exception as e:
            logger.error(
                "embedding_query_failed",
                error=str(e),
                text_length=len(normalized_text),
                provider=self.provider,
            )
            raise

    async def _get_cached_query_embedding(self, normalized_text: str) -> list[float] | None:
        """Read a cached query embedding without letting cache failures break requests."""
        try:
            return await self.query_cache.get(normalized_text)
        except Exception as exc:
            logger.warning("embedding_query_cache_read_failed", error=str(exc))
            return None

    async def _cache_query_embedding(self, normalized_text: str, embedding: list[float]) -> None:
        """Persist a query embedding without letting cache failures break requests."""
        try:
            await self.query_cache.set(normalized_text, embedding)
        except Exception as exc:
            logger.warning("embedding_query_cache_write_failed", error=str(exc))

    def _prepare_text(self, title: str, fields: list[str | None] | None = None) -> str:
        """
        Generic text preparation for embedding.

        Combines title with any number of optional fields.

        Args:
            title: Primary text (e.g., session/event title)
            fields: List of optional text fields to append

        Returns:
            Combined text for embedding
        """
        parts = [title]
        if fields:
            # Filter out None and empty strings
            parts.extend([f for f in fields if f])
        return " ".join(parts)

    def _prepare_session_text(
        self,
        title: str,
        description: str | None = None,
        short_description: str | None = None,
        summary: str | None = None,
    ) -> str:
        """
        Prepare text for session embedding.

        Combines title, description, short_description, and summary (if available).
        Delegates to generic _prepare_text method.

        Args:
            title: Session title
            description: Full description
            short_description: Short description
            summary: Full summary (if available)

        Returns:
            Combined text for embedding
        """
        summary_truncated = summary[:1000] if summary else None
        description = short_description if len(short_description) > 100 else description
        return self._prepare_text(
            title=title,
            fields=[description, summary_truncated],
        )

    def prepare_session_text_with_summary(self, session) -> str:
        """
        Prepare text for session embedding with summary fetching.

        Fetches the session's summary from the content table and combines it
        with title and short_description for embedding.

        Args:
            session: Session entity (must have id, title, short_description attributes)

        Returns:
            Combined text for embedding

        Raises:
            Exception: If text validation fails
        """
        # Try to fetch summary from content table
        summary_text = None
        db = SessionLocal()
        try:
            summary_content = content_crud.get_content_by_identifier(db, session.id, "summary")
            summary_text = summary_content.content if summary_content else None
        except Exception as e:
            logger.debug(
                "embedding_summary_fetch_failed",
                session_id=session.id,
                error=str(e),
            )
        finally:
            db.close()

        # Prepare text with fetched summary
        return self._prepare_session_text(
            title=session.title,
            description=getattr(session, "description", None),
            short_description=getattr(session, "short_description", None),
            summary=summary_text,
        )

    @staticmethod
    def validate_embedding_text(text: str, max_length: int = MAX_EMBEDDING_TEXT_LENGTH) -> bool:
        """
        Validate that text is suitable for embedding.

        Args:
            text: Text to validate
            max_length: Maximum allowed text length

        Returns:
            True if valid, False otherwise
        """
        if not EmbeddingTextHelper.validate_embedding_text(text):
            return False

        if len(text) > max_length:
            logger.warning(
                "embedding_text_too_long",
                text_length=len(text),
                max_length=max_length,
            )
            return False

        return True

    async def store_session_embedding(
        self,
        session_id: int,
        embedding: list[float],
        text: str,
        metadata: dict | None = None,
        session=None,
    ) -> None:
        """
        Store session embedding in Chroma with optional metadata for filtering.

        Args:
            session_id: Session ID
            embedding: Embedding vector
            text: Original text that was embedded
            metadata: Optional pre-built metadata dict. If not provided and session is
                given, metadata will be built from session object.
            session: Optional session entity. If provided and metadata is None, metadata
                will be automatically built from the session.

        Raises:
            Exception: If storing fails
        """
        # Build metadata from session if not provided
        if metadata is None and session is not None:
            metadata = self.metadata_builder.build_session_metadata(session)

        await self.vector_store.upsert_session(
            session_id=session_id,
            embedding=embedding,
            text=text,
            metadata=metadata,
        )

    async def search_similar_sessions(
        self,
        embedding: list[float],
        limit: int = 10,
        where: dict | None = None,
    ) -> list[tuple[int, float, str]]:
        """
        Search for similar sessions in Chroma with optional filtering.

        Args:
            embedding: Query embedding
            limit: Maximum number of results
            where: Optional Chroma where filter dict for metadata filtering
                Example: {"language": "en"}
                Complex: {"$and": [{"language": "en"}, {"status": "published"}]}
                See: https://docs.trychroma.com/usage-guide#filtering-where-documents

        Returns:
            List of tuples: (session_id, similarity_score, text)

        Raises:
            Exception: If search fails
        """
        return await self.vector_store.query_similar_sessions(
            embedding=embedding,
            limit=limit,
            where=where,
        )

    async def delete_session_embedding(self, session_id: int) -> bool:
        """
        Delete session embedding from Chroma.

        Args:
            session_id: Session ID

        Returns:
            True if deleted successfully, False if not found

        Raises:
            Exception: If deletion fails
        """
        return await self.vector_store.delete_session(session_id)

    async def get_session_embeddings(self, session_ids: list[int]) -> dict[int, list[float]]:
        """
        Retrieve stored embeddings for given session IDs.

        Used by recommendations to compute centroid when no text query is provided.

        Args:
            session_ids: List of session IDs to retrieve embeddings for

        Returns:
            Dictionary mapping session_id to embedding vector

        Raises:
            Exception: If retrieval fails
        """
        return await self.vector_store.get_session_embeddings(session_ids)
