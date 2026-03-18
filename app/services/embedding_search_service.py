"""
Semantic search orchest ration service.

Coordinates embedding generation, Chroma queries, and database fetches
to provide a clean interface for similarity search across sessions/events.
"""

from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.crud.event import event_crud
from app.crud.session import session_crud
from app.database.models import EventStatus, SessionStatus
from app.services.embedding_exceptions import (
    EmbeddingSearchError,
    InvalidEmbeddingTextError,
)
from app.services.embedding_service import EmbeddingService

logger = structlog.get_logger()


class EmbeddingSearchService:
    """Orchestrates semantic search: embedding → Chroma query → database fetch."""

    def __init__(self, embedding_service: EmbeddingService):
        """
        Initialize search service.

        Args:
            embedding_service: Initialized EmbeddingService instance
        """
        self.embedding_service = embedding_service

    async def search_by_collection(
        self,
        query: str,
        db: Session,
        search_fn: Callable,
        crud_read: Callable,
        status_filter: Any,
        limit: int = 10,
        extra_filter: Callable[[Any], bool] | None = None,
        entity_name: str = "entity",
        chroma_where: dict | None = None,
    ) -> list:
        """
        Generic search across any collection with optional Chroma filtering.

        Args:
            query: Search query text
            db: Database session
            search_fn: Function to call on embedding_service (e.g., search_similar_sessions)
            crud_read: CRUD read function to fetch entities (e.g., session_crud.read)
            status_filter: Status enum value to filter by (e.g., SessionStatus.PUBLISHED)
            limit: Maximum results (1-100)
            extra_filter: Optional callback for additional filtering (e.g., event_id filter)
            entity_name: Name for logging
            chroma_where: Optional Chroma where filter dict for metadata filtering at search time

        Returns:
            List of similar entities

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If search fails
        """
        if not EmbeddingService.validate_embedding_text(query):
            raise InvalidEmbeddingTextError("Query text is invalid or too long")

        try:
            # Generate embedding for query
            query_embedding = await self.embedding_service.embed_query(query)

            # Search Chroma for similar IDs with optional metadata filters
            if chroma_where:
                chroma_results = await search_fn(query_embedding, limit=limit, where=chroma_where)
            else:
                chroma_results = await search_fn(query_embedding, limit=limit)

            # Extract IDs from Chroma results
            entity_ids = [entity_id for entity_id, _, _ in chroma_results]

            # Fetch full entities from database, apply filters
            entities = []
            for entity_id in entity_ids:
                entity = crud_read(db, entity_id)
                if entity and entity.status == status_filter:
                    # Apply extra filter if provided
                    if extra_filter and not extra_filter(entity):
                        continue
                    entities.append(entity)

            logger.info(
                f"{entity_name}_search_completed",
                query_length=len(query),
                chroma_results=len(chroma_results),
                database_results=len(entities),
                limit=limit,
                chroma_filters_applied=bool(chroma_where),
            )

            return entities

        except InvalidEmbeddingTextError:
            raise
        except Exception as e:
            logger.error(
                f"{entity_name}_search_failed",
                error=str(e),
                error_type=type(e).__name__,
                query_length=len(query),
                limit=limit,
            )
            raise EmbeddingSearchError(f"{entity_name} search failed: {e!s}") from e

    async def search_sessions(
        self,
        query: str,
        db: Session,
        limit: int = 10,
        event_id: int | None = None,
        session_format: str | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        start_after: Any | None = None,
        start_before: Any | None = None,
        end_after: Any | None = None,
        end_before: Any | None = None,
    ) -> list:
        """
        Search for similar sessions by query text with optional filtering.

        Args:
            query: Search query text
            db: Database session
            limit: Maximum results (1-100)
            event_id: Optional filter by event ID (applied at DB level)
            session_format: Optional filter by format (applied via Chroma metadata)
            tags: Optional filter by tags (applied via Chroma metadata)
            location: Optional filter by location (applied via Chroma metadata)
            language: Optional filter by language (applied via Chroma metadata)
            duration_min: Optional minimum duration in minutes (applied via Chroma metadata)
            duration_max: Optional maximum duration in minutes (applied via Chroma metadata)
            start_after: Optional datetime filter for sessions starting after (applied via Chroma metadata)
            start_before: Optional datetime filter for sessions starting before (applied via Chroma metadata)
            end_after: Optional datetime filter for sessions ending after (applied via Chroma metadata)
            end_before: Optional datetime filter for sessions ending before (applied via Chroma metadata)

        Returns:
            List of similar SessionResponse objects

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If search fails
        """
        # Build Chroma where filter for metadata filtering
        chroma_where = None
        if (
            session_format
            or tags
            or location
            or language
            or duration_min
            or duration_max
            or start_after
            or start_before
            or end_after
            or end_before
        ):
            conditions = []

            if session_format:
                conditions.append({"session_format": session_format})
            if language:
                conditions.append({"language": language})
            if location:
                # Build OR condition for locations (match any location)
                # Only use $or if multiple locations, otherwise use direct condition
                location_conditions = [{"location": loc} for loc in location]
                if len(location_conditions) == 1:
                    conditions.append(location_conditions[0])
                else:
                    conditions.append({"$or": location_conditions})
            if tags:
                # Build OR condition for tags (match any tag)
                # Only use $or if multiple tags, otherwise use direct condition
                tag_conditions = [{"tags": {"$contains": tag}} for tag in tags]
                if len(tag_conditions) == 1:
                    conditions.append(tag_conditions[0])
                else:
                    conditions.append({"$or": tag_conditions})
            if duration_min is not None:
                conditions.append({"duration": {"$gte": duration_min}})
            if duration_max is not None:
                conditions.append({"duration": {"$lte": duration_max}})
            if start_after is not None:
                conditions.append({"start_datetime": {"$gte": start_after.timestamp()}})
            if start_before is not None:
                conditions.append({"start_datetime": {"$lte": start_before.timestamp()}})
            if end_after is not None:
                conditions.append({"end_datetime": {"$gte": end_after.timestamp()}})
            if end_before is not None:
                conditions.append({"end_datetime": {"$lte": end_before.timestamp()}})

            # Combine all conditions with AND
            if len(conditions) == 1:
                chroma_where = conditions[0]
            elif len(conditions) > 1:
                chroma_where = {"$and": conditions}

        # Optional filter for event_id (applied at DB level since it's not in Chroma)
        event_filter = None
        if event_id:
            event_filter = lambda s: s.event_id == event_id

        return await self.search_by_collection(
            query=query,
            db=db,
            search_fn=self.embedding_service.search_similar_sessions,
            crud_read=session_crud.read,
            status_filter=SessionStatus.PUBLISHED,
            limit=limit,
            extra_filter=event_filter,
            entity_name="session",
            chroma_where=chroma_where,
        )
