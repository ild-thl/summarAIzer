"""Embedding provider package."""

from app.services.embedding.providers.factory import create_embeddings_backend

__all__ = ["create_embeddings_backend"]
