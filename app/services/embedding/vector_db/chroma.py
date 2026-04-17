"""Chroma client and collection initialization helpers."""

from __future__ import annotations

from typing import Any

import chromadb
import structlog
from chromadb.config import Settings as ChromaSettings

from app.constants.embedding import COLLECTION_METADATA_COSINE
from app.services.embedding.protocols import ChromaClientProtocol, ChromaCollectionProtocol

logger = structlog.get_logger()


class ChromaInitializer:
    """Encapsulates Chroma client and collection bootstrap logic."""

    @staticmethod
    def create_client(
        chroma_url: str,
        chroma_tenant: str,
        chroma_credentials: str | None,
        chroma_provider: str | None,
    ) -> ChromaClientProtocol:

        chroma_settings = ChromaInitializer._init_chroma_settings(
            chroma_credentials=chroma_credentials,
            chroma_provider=chroma_provider,
        )

        client = chromadb.HttpClient(
            host=chroma_url,
            settings=chroma_settings,
            tenant=chroma_tenant,
        )
        logger.info(
            "chroma_client_initialized",
            url=chroma_url,
        )
        return client

    @staticmethod
    def _init_chroma_settings(
        chroma_credentials: str | None,
        chroma_provider: str | None,
    ) -> ChromaSettings:
        if chroma_credentials and chroma_provider:
            logger.info(
                "chroma_settings_auth_enabled",
                provider=chroma_provider,
            )
            return ChromaSettings(
                chroma_client_auth_provider=chroma_provider,
                chroma_client_auth_credentials=chroma_credentials,
                chroma_auth_token_transport_header="Authorization",
                anonymized_telemetry=False,
            )
        else:
            logger.info("chroma_settings_no_auth")
            return ChromaSettings(
                anonymized_telemetry=False,
            )

    @staticmethod
    def init_collection(
        chroma_client: ChromaClientProtocol,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChromaCollectionProtocol:
        collection_metadata = metadata or COLLECTION_METADATA_COSINE
        try:
            collection = chroma_client.get_or_create_collection(
                name=name,
                metadata=collection_metadata,
            )
            logger.info("collection_created", name=name)
            return collection
        except Exception as e:
            logger.debug(
                "collection_creation_with_metadata_failed",
                name=name,
                error=str(e),
            )
            try:
                collection = chroma_client.get_collection(name=name)
                logger.info("collection_retrieved_existing", name=name)
                return collection
            except Exception as e2:
                logger.error(
                    "collection_initialization_failed",
                    name=name,
                    error=str(e2),
                )
                raise
