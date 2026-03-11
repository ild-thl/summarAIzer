"""
Factory for creating embedding services with dependency injection.

Centralizes service initialization and configuration validation,
reducing boilerplate in routes and tasks.
"""

import structlog
from functools import lru_cache

from app.config.settings import get_settings
from app.services.embedding_service import EmbeddingService
from app.services.embedding_search_service import EmbeddingSearchService
from app.services.embedding_exceptions import ChromaConnectionError

logger = structlog.get_logger()


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """
    Factory function for EmbeddingService with dependency injection.

    Uses lru_cache to ensure single instance across request scope.
    Validates Chroma connectivity at initialization.

    Returns:
        Initialized EmbeddingService instance

    Raises:
        ChromaConnectionError: If Chroma server is unreachable
        ValueError: If settings are invalid
    """
    settings = get_settings()

    if not settings.enable_embeddings:
        logger.info("embeddings_disabled_by_config")
        return None

    try:
        service = EmbeddingService(
            embedding_provider=settings.embedding_provider,
            embedding_api_key=settings.embedding_api_key,
            embedding_api_base_url=settings.embedding_api_base_url,
            embedding_model_name=settings.embedding_model_name,
            chroma_host=settings.chroma_host,
            chroma_port=settings.chroma_port,
            chroma_tenant=settings.chroma_tenant,
            chroma_credentials=settings.chroma_credentials,
            chroma_provider=settings.chroma_provider,
            embedding_dimension=settings.embedding_dimension,
        )

        logger.info(
            "embedding_service_created",
            provider=settings.embedding_provider,
            chroma_url=f"{settings.chroma_host}:{settings.chroma_port}",
        )

        return service

    except Exception as e:
        logger.error(
            "embedding_service_creation_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise ChromaConnectionError(
            f"Failed to initialize embedding service: {str(e)}"
        ) from e


def get_search_service() -> EmbeddingSearchService:
    """
    Factory for EmbeddingSearchService.

    Returns:
        EmbeddingSearchService with injected EmbeddingService

    Raises:
        ChromaConnectionError: If underlying service initialization fails
    """
    embedding_service = get_embedding_service()

    if embedding_service is None:
        raise ChromaConnectionError("Embeddings are disabled")

    return EmbeddingSearchService(embedding_service)


def reset_services():
    """
    Reset cached services (useful for testing).

    Clears the lru_cache to force re-initialization on next call.
    """
    get_embedding_service.cache_clear()
    logger.debug("embedding_services_cache_cleared")
