"""Embedding service for semantic search using configurable embeddings and Chroma storage."""

import chromadb
import structlog
from chromadb.config import Settings as ChromaSettings

from app.constants.embedding import (
    COLLECTION_METADATA_COSINE,
    MAX_EMBEDDING_TEXT_LENGTH,
    SESSIONS_COLLECTION,
)
from app.crud import generated_content as content_crud
from app.database.connection import SessionLocal
from app.services.embeddings_manager import create_embeddings_backend

logger = structlog.get_logger()


class EmbeddingService:
    """Service for generating embeddings and storing/searching vectors in Chroma."""

    def __init__(
        self,
        embedding_provider: str = "huggingface",
        embedding_api_key: str | None = None,
        embedding_api_base_url: str | None = None,
        embedding_model_name: str | None = None,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_tenant: str = "default_tenant",
        chroma_credentials: str | None = None,
        chroma_provider: str | None = None,
        embedding_dimension: int = 768,
    ):
        """
        Initialize embedding service with configurable embeddings backend and Chroma vector storage.

        Args:
            embedding_provider: "openai" or "huggingface"
            embedding_api_key: API key for the embedding service
            embedding_api_base_url: Base URL for the embedding API
            embedding_model_name: Model name (used for OpenAI provider)
            chroma_host: Chroma server host
            chroma_port: Chroma server port
            chroma_tenant: Chroma tenant name
            chroma_credentials: Optional Chroma authentication token
            chroma_provider: Optional Chroma authentication provider
            embedding_dimension: Expected embedding dimension (for validation)
        """
        self.provider = embedding_provider
        self.embedding_dimension = embedding_dimension

        try:
            # Initialize embeddings backend based on provider
            if embedding_provider == "openai":
                if not all([embedding_api_key, embedding_api_base_url, embedding_model_name]):
                    raise ValueError(
                        "OpenAI embeddings requires: embedding_api_key, embedding_api_base_url, embedding_model_name"
                    )
                self.embeddings = create_embeddings_backend(
                    provider="openai",
                    api_key=embedding_api_key,
                    base_url=embedding_api_base_url,
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
                )
                logger.info(
                    "embedding_backend_initialized",
                    provider="huggingface",
                    api_base_url=embedding_api_base_url,
                )
            else:
                raise ValueError(
                    f"Unknown embedding provider: {embedding_provider}. Use 'openai' or 'huggingface'"
                )

            # Initialize Chroma client
            if chroma_credentials and chroma_provider:
                logger.info(
                    "chroma_client_auth_enabled",
                    provider=chroma_provider,
                    host=chroma_host,
                    port=chroma_port,
                )

                self.chroma_client = chromadb.HttpClient(
                    host=chroma_host,
                    port=chroma_port,
                    settings=ChromaSettings(
                        chroma_client_auth_provider=chroma_provider,
                        chroma_client_auth_credentials=chroma_credentials,
                        chroma_auth_token_transport_header="Authorization",
                        anonymized_telemetry=False,
                    ),
                    tenant=chroma_tenant,
                )
                logger.info(
                    "chroma_client_initialized",
                    host=chroma_host,
                    port=chroma_port,
                    auth_enabled=True,
                )
            else:
                logger.info(
                    "chroma_client_no_auth",
                    host=chroma_host,
                    port=chroma_port,
                )
                self.chroma_client = chromadb.HttpClient(
                    host=chroma_host,
                    port=chroma_port,
                )
                logger.info(
                    "chroma_client_initialized",
                    host=chroma_host,
                    port=chroma_port,
                    auth_enabled=False,
                )

            # Get or create collections with explicit configuration
            self.sessions_collection = self._init_collection(SESSIONS_COLLECTION)
            logger.info("embedding_collections_ready", sessions=True)

        except Exception as e:
            logger.error(
                "embedding_service_initialization_failed",
                error=str(e),
                provider=embedding_provider,
                chroma_host=chroma_host,
                chroma_port=chroma_port,
            )
            raise

    def _init_collection(self, name: str) -> "chromadb.Collection":
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
        try:
            collection = self.chroma_client.get_or_create_collection(
                name=name,
                metadata=COLLECTION_METADATA_COSINE,
            )
            logger.info("collection_created", name=name)
            return collection
        except Exception as e:
            logger.debug(
                "collection_creation_with_metadata_failed",
                name=name,
                error=str(e),
            )
            # Try without metadata if metadata fails
            try:
                collection = self.chroma_client.get_collection(name=name)
                logger.info("collection_retrieved_existing", name=name)
                return collection
            except Exception as e2:
                logger.error(
                    "collection_initialization_failed",
                    name=name,
                    error=str(e2),
                )
                raise

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

        try:
            logger.debug(
                "embedding_query_start",
                text_length=len(text),
                provider=self.provider,
            )

            # Use async embedding
            embedding = await self.embeddings.aembed_query(text)

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

            return embedding

        except Exception as e:
            logger.error(
                "embedding_query_failed",
                error=str(e),
                text_length=len(text) if text else 0,
                provider=self.provider,
            )
            raise

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
        short_description: str | None = None,
        summary: str | None = None,
    ) -> str:
        """
        Prepare text for session embedding.

        Combines title, short_description, and summary (if available).
        Delegates to generic _prepare_text method.

        Args:
            title: Session title
            short_description: Short description
            summary: Full summary (if available)

        Returns:
            Combined text for embedding
        """
        # Truncate summary to 1000 chars to balance detail with token limits
        summary_truncated = summary[:1000] if summary else None
        return self._prepare_text(
            title=title,
            fields=[short_description, summary_truncated],
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
            short_description=session.short_description,
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
        if not text or not text.strip():
            return False

        if len(text) > max_length:
            logger.warning(
                "embedding_text_too_long",
                text_length=len(text),
                max_length=max_length,
            )
            return False

        return True

    def _build_session_metadata(self, session) -> dict:
        """
        Build Chroma metadata dict from session object.

        Knows the structure of session model and maps it to Chroma filter metadata.
        This is the single source of truth for session metadata structure.

        Args:
            session: Session entity with attributes like title, status, tags, etc.

        Returns:
            Dictionary of metadata for Chroma filtering
        """
        return {
            "title": session.title,
            "status": session.status.value if session.status else None,
            "event_id": session.event_id if session.event_id else -1,
            "session_format": session.session_format.value if session.session_format else None,
            "tags": session.tags or None,
            "language": session.language or "en",
            "duration": session.duration if session.duration else -1,
            "speakers": session.speakers or [],
        }

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
        try:
            # Build metadata from session if not provided
            if metadata is None and session is not None:
                metadata = self._build_session_metadata(session)

            # Build metadata with session_id and type always included
            chroma_metadata = {"session_id": session_id, "type": "session"}
            if metadata:
                chroma_metadata.update(metadata)

            self.sessions_collection.upsert(
                ids=[f"session_{session_id}"],
                embeddings=[embedding],
                documents=[text],
                metadatas=[chroma_metadata],
            )
            logger.info(
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
        try:
            query_kwargs = {
                "query_embeddings": [embedding],
                "n_results": limit,
            }
            if where:
                query_kwargs["where"] = where

            results = self.sessions_collection.query(**query_kwargs)

            # Extract session_ids and similarity scores
            output = []
            if results["ids"] and len(results["ids"]) > 0:
                for i, chroma_id in enumerate(results["ids"][0]):
                    session_id = int(chroma_id.split("_")[1])
                    # Chroma returns distances, convert to similarity (1 - distance for cosine)
                    similarity = 1 - results["distances"][0][i]
                    text = results["documents"][0][i] if results["documents"] else ""
                    output.append((session_id, similarity, text))

            logger.info(
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
        try:
            chroma_id = f"session_{session_id}"
            self.sessions_collection.delete(ids=[chroma_id])
            logger.info("session_embedding_deleted", session_id=session_id)
            return True
        except Exception as e:
            logger.error(
                "session_embedding_deletion_failed",
                session_id=session_id,
                error=str(e),
            )
            raise
