"""Embeddings manager supporting multiple embedding backends."""

import structlog
from typing import List, Optional
from abc import ABC, abstractmethod
import requests

logger = structlog.get_logger()


class EmbeddingsBackend(ABC):
    """Abstract base class for embedding backends."""

    @abstractmethod
    async def aembed_query(self, text: str) -> List[float]:
        """Asynchronously embed a query text."""
        pass


class OpenAIEmbeddingsBackend(EmbeddingsBackend):
    """OpenAI embeddings backend using langchain."""

    def __init__(self, api_key: str, api_base_url: str, model: str):
        """Initialize OpenAI embeddings."""
        from langchain_openai import OpenAIEmbeddings

        self.model = model
        self.embeddings = OpenAIEmbeddings(
            model=model,
            api_key=api_key,
            base_url=api_base_url,
        )
        logger.info(
            "openai_embeddings_initialized",
            model=model,
            api_base_url=api_base_url,
        )

    async def aembed_query(self, text: str) -> List[float]:
        """Embed query using OpenAI."""
        return await self.embeddings.aembed_query(text)


class HuggingFaceInferenceEmbeddingsBackend(EmbeddingsBackend):
    """HuggingFace Inference API embeddings backend."""

    def __init__(self, api_key: str, api_base_url: str):
        """Initialize HuggingFace embeddings."""
        self.api_key = api_key
        self.api_base_url = api_base_url
        logger.info(
            "huggingface_embeddings_initialized",
            api_base_url=api_base_url,
        )

    async def aembed_query(self, text: str) -> List[float]:
        """Embed query using HuggingFace Inference API."""
        return self.embed_query(text)

    def embed_query(self, text: str) -> List[float]:
        """Embed query text synchronously."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        response = requests.post(
            self.api_base_url,
            json={"inputs": [text]},
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()

        # Handle different response formats
        if isinstance(result, list) and len(result) > 0:
            if isinstance(result[0], list):
                return result[0]
            elif isinstance(result[0], dict) and "embedding" in result[0]:
                return result[0]["embedding"]

        raise ValueError(f"Unexpected response format from HuggingFace API: {result}")


def create_embeddings_backend(provider: str, **kwargs) -> EmbeddingsBackend:
    """
    Factory function to create embeddings backend.

    Args:
        provider: "openai" or "huggingface"
        **kwargs: Provider-specific configuration

    Returns:
        EmbeddingsBackend instance

    Raises:
        ValueError: If provider is unknown or config is invalid
    """
    if provider == "openai":
        required_keys = {"api_key", "api_base_url", "model"}
        if not required_keys.issubset(kwargs.keys()):
            raise ValueError(
                f"OpenAI embeddings requires: {required_keys}. Got: {kwargs.keys()}"
            )
        return OpenAIEmbeddingsBackend(
            api_key=kwargs["api_key"],
            api_base_url=kwargs["api_base_url"],
            model=kwargs["model"],
        )
    elif provider == "huggingface":
        required_keys = {"api_key", "api_base_url"}
        if not required_keys.issubset(kwargs.keys()):
            raise ValueError(
                f"HuggingFace embeddings requires: {required_keys}. Got: {kwargs.keys()}"
            )
        return HuggingFaceInferenceEmbeddingsBackend(
            api_key=kwargs["api_key"],
            api_base_url=kwargs["api_base_url"],
        )
    else:
        raise ValueError(
            f"Unknown embeddings provider: {provider}. Use 'openai' or 'huggingface'"
        )
