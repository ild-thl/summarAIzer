"""Semantic search service focused on Chroma-backed retrieval."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.services.embedding_exceptions import EmbeddingSearchError, InvalidEmbeddingTextError
from app.services.embedding_service import EmbeddingService

logger = structlog.get_logger()


class EmbeddingSearchService:
    """Orchestrates semantic search: embedding -> Chroma query -> database fetch."""

    def __init__(self, embedding_service: EmbeddingService):
        self.embedding_service = embedding_service

    @staticmethod
    def _extract_window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
        """Extract (start, end) from TimeWindow objects or plain dicts."""
        if isinstance(window, dict):
            return window.get("start"), window.get("end")
        return getattr(window, "start", None), getattr(window, "end", None)

    def _build_location_condition(self, location: list[str] | None) -> dict | None:
        if not location:
            return None
        location_conditions = [{"location": loc} for loc in location]
        if len(location_conditions) == 1:
            return location_conditions[0]
        return {"$or": location_conditions}

    def _build_tags_condition(self, tags: list[str] | None) -> dict | None:
        if not tags:
            return None
        tag_conditions = [{"tags": {"$contains": tag}} for tag in tags]
        if len(tag_conditions) == 1:
            return tag_conditions[0]
        return {"$or": tag_conditions}

    def _build_time_windows_conditions(self, time_windows: list[Any] | None) -> dict | None:
        if not time_windows:
            return None

        window_conditions: list[dict[str, Any]] = []
        for window in time_windows:
            start, end = self._extract_window_bounds(window)
            if start is None or end is None:
                continue
            window_conditions.append(
                {
                    "$and": [
                        {"start_datetime": {"$gte": start.timestamp()}},
                        {"end_datetime": {"$lte": end.timestamp()}},
                    ]
                }
            )

        if not window_conditions:
            return None
        if len(window_conditions) == 1:
            return window_conditions[0]
        return {"$or": window_conditions}

    @staticmethod
    def _build_simple_conditions(
        session_format: str | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
    ) -> list[dict]:
        conditions = []
        if session_format:
            conditions.append({"session_format": session_format})
        if language:
            conditions.append({"language": language})
        if duration_min is not None:
            conditions.append({"duration": {"$gte": duration_min}})
        if duration_max is not None:
            conditions.append({"duration": {"$lte": duration_max}})
        return conditions

    def _build_chroma_conditions(
        self,
        session_format: str | None = None,
        tags: list[str] | None = None,
        location: list[str] | None = None,
        language: str | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        time_windows: list[Any] | None = None,
    ) -> dict | None:
        """Build Chroma WHERE filter conditions from search parameters."""
        all_conditions = []
        all_conditions.extend(
            self._build_simple_conditions(session_format, language, duration_min, duration_max)
        )

        location_condition = self._build_location_condition(location)
        if location_condition:
            all_conditions.append(location_condition)

        tags_condition = self._build_tags_condition(tags)
        if tags_condition:
            all_conditions.append(tags_condition)

        time_window_condition = self._build_time_windows_conditions(time_windows)
        if time_window_condition:
            all_conditions.append(time_window_condition)

        if not all_conditions:
            return None
        if len(all_conditions) == 1:
            return all_conditions[0]
        return {"$and": all_conditions}

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
        """Generic search across any Chroma-backed collection."""
        if not EmbeddingService.validate_embedding_text(query):
            raise InvalidEmbeddingTextError("Query text is invalid or too long")

        try:
            query_embedding = await self.embedding_service.embed_query(query)
            if chroma_where:
                chroma_results = await search_fn(query_embedding, limit=limit, where=chroma_where)
            else:
                chroma_results = await search_fn(query_embedding, limit=limit)

            entity_ids = [entity_id for entity_id, _, _ in chroma_results]

            entities = []
            for entity_id in entity_ids:
                entity = crud_read(db, entity_id)
                if entity and entity.status == status_filter:
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
        time_windows: list[Any] | None = None,
    ) -> list[tuple]:
        """Search for similar published sessions with optional metadata filters."""
        if not EmbeddingService.validate_embedding_text(query):
            raise InvalidEmbeddingTextError("Query text is invalid or too long")

        try:
            query_embedding = await self.embedding_service.embed_query(query)
            chroma_where = self._build_chroma_conditions(
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
            )

            if chroma_where:
                chroma_results = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=limit, where=chroma_where
                )
            else:
                chroma_results = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=limit
                )

            results = []
            for session_id, chroma_similarity, _ in chroma_results:
                session = session_crud.read(db, session_id)
                if not session or session.status != SessionStatus.PUBLISHED:
                    continue
                if event_id and session.event_id != event_id:
                    continue

                scores = {
                    "overall_score": round(chroma_similarity, 3),
                    "semantic_similarity": round(chroma_similarity, 3),
                    "liked_cluster_similarity": None,
                    "disliked_similarity": None,
                    "filter_match_ratio": 1.0,
                    "explanation": f"semantic similarity: {chroma_similarity:.2f}",
                }
                results.append((session, scores))
                if len(results) >= limit:
                    break

            logger.info(
                "session_search_completed",
                query_length=len(query),
                chroma_results=len(chroma_results),
                database_results=len(results),
                limit=limit,
            )
            return results

        except InvalidEmbeddingTextError:
            raise
        except EmbeddingSearchError:
            raise
        except Exception as e:
            logger.error(
                "session_search_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmbeddingSearchError(f"Session search failed: {e!s}") from e
