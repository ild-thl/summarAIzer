"""
Custom exceptions for embedding features.

Provides semantic, typed exceptions for embedding operations,
enabling clean error handling without string matching.
"""


class EmbeddingError(Exception):
    """Base exception for embedding operations."""

    pass


class EmbeddingGenerationError(EmbeddingError):
    """Raised when embedding generation fails (non-transient)."""

    pass


class EmbeddingStorageError(EmbeddingError):
    """Raised when Chroma storage operations fail."""

    pass


class EmbeddingSearchError(EmbeddingError):
    """Raised when semantic search fails."""

    pass


class InvalidEmbeddingTextError(EmbeddingError):
    """Raised when query/source text is invalid for embedding."""

    pass


class ChromaConnectionError(EmbeddingError):
    """Raised when Chroma server connection fails."""

    pass
