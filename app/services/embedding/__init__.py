"""Embedding domain package."""

from app.services.embedding.exceptions import (
    ChromaConnectionError,
    EmbeddingError,
    EmbeddingGenerationError,
    EmbeddingSearchError,
    EmbeddingStorageError,
    InvalidEmbeddingTextError,
)
from app.services.embedding.factory import (
    get_embedding_service,
    get_recommendation_service,
    get_search_service,
    reset_services,
)
from app.services.embedding.search_service import EmbeddingSearchService
from app.services.embedding.service import EmbeddingService

__all__ = [
    "EmbeddingError",
    "EmbeddingGenerationError",
    "EmbeddingStorageError",
    "EmbeddingSearchError",
    "InvalidEmbeddingTextError",
    "ChromaConnectionError",
    "EmbeddingService",
    "EmbeddingSearchService",
    "get_embedding_service",
    "get_search_service",
    "get_recommendation_service",
    "reset_services",
]
