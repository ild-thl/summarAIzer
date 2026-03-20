"""
Semantic search orchest ration service.

Coordinates embedding generation, Chroma queries, and database fetches
to provide a clean interface for similarity search across sessions/events.
"""

from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import SessionStatus
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

    def _build_location_condition(self, location: list[str] | None) -> dict | None:
        """Build location condition for Chroma filtering."""
        if not location:
            return None
        location_conditions = [{"location": loc} for loc in location]
        if len(location_conditions) == 1:
            return location_conditions[0]
        return {"$or": location_conditions}

    def _build_tags_condition(self, tags: list[str] | None) -> dict | None:
        """Build tags condition for Chroma filtering."""
        if not tags:
            return None
        tag_conditions = [{"tags": {"$contains": tag}} for tag in tags]
        if len(tag_conditions) == 1:
            return tag_conditions[0]
        return {"$or": tag_conditions}

    def _build_datetime_range_conditions(
        self,
        start_after: Any | None,
        start_before: Any | None,
        end_after: Any | None,
        end_before: Any | None,
    ) -> list[dict]:
        """Build datetime range conditions for Chroma filtering."""
        conditions = []
        if start_after is not None:
            conditions.append({"start_datetime": {"$gte": start_after.timestamp()}})
        if start_before is not None:
            conditions.append({"start_datetime": {"$lte": start_before.timestamp()}})
        if end_after is not None:
            conditions.append({"end_datetime": {"$gte": end_after.timestamp()}})
        if end_before is not None:
            conditions.append({"end_datetime": {"$lte": end_before.timestamp()}})
        return conditions

    def _build_simple_conditions(
        self,
        session_format: str | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
    ) -> list[dict]:
        """Build simple field conditions for Chroma filtering."""
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
        start_after: Any | None = None,
        start_before: Any | None = None,
        end_after: Any | None = None,
        end_before: Any | None = None,
    ) -> dict | None:
        """Build Chroma WHERE filter conditions from parameters.

        Delegates to helper methods for different condition types.
        Returns None if no conditions, dict otherwise.
        """
        # Collect all conditions
        all_conditions = []
        all_conditions.extend(
            self._build_simple_conditions(session_format, language, duration_min, duration_max)
        )

        # Add complex conditions
        location_condition = self._build_location_condition(location)
        if location_condition:
            all_conditions.append(location_condition)

        tags_condition = self._build_tags_condition(tags)
        if tags_condition:
            all_conditions.append(tags_condition)

        all_conditions.extend(
            self._build_datetime_range_conditions(start_after, start_before, end_after, end_before)
        )

        # Combine conditions
        if not all_conditions:
            return None
        if len(all_conditions) == 1:
            return all_conditions[0]
        return {"$and": all_conditions}

    def _combine_conditions(self, condition1: dict | None, condition2: dict | None) -> dict | None:
        """Combine two Chroma WHERE conditions with AND logic."""
        if not condition1 and not condition2:
            return None
        if not condition1:
            return condition2
        if not condition2:
            return condition1
        return {"$and": [condition1, condition2]}

    def _has_active_filters(
        self,
        event_id: int | None,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        start_after: Any | None,
        start_before: Any | None,
        end_after: Any | None,
        end_before: Any | None,
    ) -> bool:
        """Check if any filters are active."""
        return bool(
            event_id
            or session_format
            or tags
            or location
            or language
            or duration_min
            or duration_max
            or start_after
            or start_before
            or end_after
            or end_before
        )

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
    ) -> list[tuple]:
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
            List of tuples: (session, scores_dict) with similarity metrics

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If search fails
        """
        if not EmbeddingService.validate_embedding_text(query):
            raise InvalidEmbeddingTextError("Query text is invalid or too long")

        try:
            # Generate embedding for query
            query_embedding = await self.embedding_service.embed_query(query)

            # Build Chroma where filter using helper
            chroma_where = self._build_chroma_conditions(
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                start_after=start_after,
                start_before=start_before,
                end_after=end_after,
                end_before=end_before,
            )

            # Search Chroma with filters
            if chroma_where:
                chroma_results = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=limit, where=chroma_where
                )
            else:
                chroma_results = await self.embedding_service.search_similar_sessions(
                    query_embedding, limit=limit
                )

            # Fetch from DB, apply filters, and compute scores
            results = []
            for session_id, chroma_similarity, _ in chroma_results:
                session = session_crud.read(db, session_id)
                if not session or session.status != SessionStatus.PUBLISHED:
                    continue

                # Apply event_id filter if provided
                if event_id and session.event_id != event_id:
                    continue

                # Compute scores for search results
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

    async def recommend_sessions(
        self,
        db: Session,
        accepted_ids: list[int] | None = None,
        rejected_ids: list[int] | None = None,
        query: str | None = None,
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
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
    ) -> list[tuple]:
        """
        Recommend sessions based on user preferences and optional filters.

        **Phase 2 Features:** Similarity-based re-ranking using embedding comparisons.
        Final score = base_score + a*liked_sim - b*disliked_sim

        Supports three execution modes:
        1. With query: Semantic search using provided text
        2. With accepted_ids (no query): Centroid-based search from liked sessions
        3. No query & no accepted_ids: CRUD fallback with efficient filtering (filters + exclusions)

        All inputs are optional. Rejected IDs are excluded from results in all modes.

        Args:
            db: Database session
            accepted_ids: Session IDs the user has liked (centroid source if no query)
            rejected_ids: Session IDs the user has disliked (always excluded)
            query: Optional text query for semantic search
            limit: Maximum results (1-100)
            event_id: Optional filter by event ID
            session_format: Optional filter by format
            tags: Optional filter by tags (OR logic)
            location: Optional filter by location (OR logic)
            language: Optional filter by language
            duration_min: Optional minimum duration in minutes
            duration_max: Optional maximum duration in minutes
            start_after: Optional datetime filter for sessions starting after
            start_before: Optional datetime filter for sessions starting before
            end_after: Optional datetime filter for sessions ending after
            end_before: Optional datetime filter for sessions ending before
            liked_embedding_weight: Weight (a) to boost sessions similar to liked sessions (0-1, default 0.3)
            disliked_embedding_weight: Weight (b) to penalize sessions similar to disliked sessions (0-1, default 0.2)

        Returns:
            List of tuples: (session, scores_dict) where scores_dict contains:
            - overall_score (0-1): Reranked score using Phase 2 formula
            - semantic_similarity (0-1 or None): Query similarity from Chroma
            - liked_cluster_similarity (0-1 or None): Centroid similarity (Phase 2)
            - disliked_similarity (0-1 or None): Max similarity to disliked sessions (Phase 2)
            - filter_match_ratio (0-1): Matching filters / total active filters
            - explanation (str): Human-readable summary

        Raises:
            InvalidEmbeddingTextError: If query is invalid
            EmbeddingSearchError: If recommendation fails
        """
        # Normalize inputs
        accepted_ids = accepted_ids or []
        rejected_ids = rejected_ids or []
        seen_ids = set(accepted_ids + rejected_ids)

        try:
            # Path 3: CRUD Fallback (no query AND no accepted_ids)
            # Most efficient for filters-only queries
            if not query and not accepted_ids:
                active_filters = self._has_active_filters(
                    event_id=event_id,
                    session_format=session_format,
                    tags=tags,
                    location=location,
                    language=language,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    start_after=start_after,
                    start_before=start_before,
                    end_after=end_after,
                    end_before=end_before,
                )
                logger.info(
                    "recommendation_crud_fallback",
                    rejected_ids_count=len(rejected_ids),
                    has_filters=active_filters,
                )
                return await self._recommend_fallback(
                    db,
                    rejected_ids=rejected_ids,
                    event_id=event_id,
                    session_format=session_format,
                    tags=tags,
                    location=location,
                    language=language,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    start_after=start_after,
                    start_before=start_before,
                    end_after=end_after,
                    end_before=end_before,
                    limit=limit,
                )

            # Path 1 & 2: Semantic Search (query OR accepted_ids provided)
            # Determine query embedding
            query_embedding, semantic_similarity_enabled = await self._determine_query_embedding(
                query=query,
                accepted_ids=accepted_ids,
                rejected_ids=rejected_ids,
            )

            # Build metadata filters
            metadata_conditions = self._build_chroma_conditions(
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                start_after=start_after,
                start_before=start_before,
                end_after=end_after,
                end_before=end_before,
            )

            # Add seen-session exclusion with $nin operator
            nin_condition = {"session_id": {"$nin": list(seen_ids)}} if seen_ids else None
            chroma_where = self._combine_conditions(nin_condition, metadata_conditions)

            # Search Chroma (no over-fetch needed with $nin operator)
            try:
                if chroma_where:
                    chroma_results = await self.embedding_service.search_similar_sessions(
                        query_embedding, limit=limit, where=chroma_where
                    )
                else:
                    chroma_results = await self.embedding_service.search_similar_sessions(
                        query_embedding, limit=limit
                    )
            except Exception as e:
                logger.error(
                    "recommendation_chroma_search_failed",
                    error=str(e),
                )
                raise EmbeddingSearchError(f"Semantic search failed: {e!s}") from e

            # Fetch from DB, apply additional filters, and compute scores with Phase 2 re-ranking
            recommendations = await self._process_chroma_recommendations(
                chroma_results=chroma_results,
                db=db,
                semantic_similarity_enabled=semantic_similarity_enabled,
                accepted_ids=accepted_ids,
                rejected_ids=rejected_ids,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                event_id=event_id,
                limit=limit,
            )

            logger.info(
                "recommendation_completed",
                chroma_results=len(chroma_results),
                seen_sessions_count=len(seen_ids),
                recommendations=len(recommendations),
                limit=limit,
            )

            return recommendations

        except InvalidEmbeddingTextError:
            raise
        except EmbeddingSearchError:
            raise
        except Exception as e:
            logger.error(
                "recommendation_failed",
                error=str(e),
                error_type=type(e).__name__,
                accepted_ids_count=len(accepted_ids),
                rejected_ids_count=len(rejected_ids),
            )
            raise EmbeddingSearchError(f"Recommendation failed: {e!s}") from e

    async def _process_chroma_recommendations(
        self,
        chroma_results: list,
        db: Session,
        semantic_similarity_enabled: bool,
        accepted_ids: list[int],
        rejected_ids: list[int],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        event_id: int | None,
        limit: int,
    ) -> list[tuple]:
        """Process Chroma search results into recommendations with Phase 2 re-ranking.

        Fetches sessions from DB, applies filters, retrieves embeddings, and computes scores
        using the Phase 2 re-ranking formula.

        Args:
            chroma_results: Results from Chroma search (session_id, similarity, text)
            db: Database session
            semantic_similarity_enabled: Whether query-based search was used
            accepted_ids: Liked session IDs
            rejected_ids: Disliked session IDs
            liked_embedding_weight: Weight to boost liked-session similarities
            disliked_embedding_weight: Weight to penalize disliked-session similarities
            event_id: Optional event filter
            limit: Max recommendations to return

        Returns:
            List of tuples: (session, scores_dict)
        """
        # Create a map of session_id -> embedding from Chroma results
        chroma_id_to_embedding = {}
        if chroma_results:
            try:
                # Fetch all embeddings in one batch
                session_ids = [session_id for session_id, _, _ in chroma_results]
                embeddings_dict = await self.embedding_service.get_session_embeddings(session_ids)
                # Reverse map: chroma_id -> embedding
                for session_id, embedding in embeddings_dict.items():
                    chroma_id_to_embedding[f"session_{session_id}"] = embedding
            except Exception as e:
                logger.warning(
                    "batch_embedding_retrieval_failed",
                    error=str(e),
                    sessions_count=len(chroma_results),
                )

        recommendations = []
        for session_id, chroma_similarity, _ in chroma_results:
            # Fetch from DB and check status
            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue

            # Apply event_id filter if provided
            if event_id and session.event_id != event_id:
                continue

            # Get embedding for this session (for Phase 2 re-ranking)
            chroma_id = f"session_{session_id}"
            session_embedding = chroma_id_to_embedding.get(chroma_id)

            if session_embedding is None:
                logger.warning(
                    "session_embedding_not_found",
                    session_id=session_id,
                )
                # Fall back to basic similarity if embedding not available
                session_embedding = [0.0] * 768  # Default placeholder (assumes 768-dim embeddings)

            # Compute scores for this recommendation using Phase 2 formula
            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                accepted_ids=accepted_ids,
                rejected_ids=rejected_ids,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
            )

            recommendations.append((session, scores))

            # Stop once we have enough recommendations
            if len(recommendations) >= limit:
                break

        # Sort by overall_score (highest first) - this is the re-ranked score
        recommendations.sort(key=lambda x: x[1]["overall_score"], reverse=True)

        return recommendations

    async def _determine_query_embedding(
        self,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> tuple[list, bool]:
        """Determine query embedding from text or liked-session centroid.

        Returns: (embedding_vector, semantic_similarity_enabled)
        """
        if query:
            if not EmbeddingService.validate_embedding_text(query):
                raise InvalidEmbeddingTextError("Query text is invalid or too long")
            query_embedding = await self.embedding_service.embed_query(query)
            logger.info(
                "recommendation_query_provided",
                query_length=len(query),
                accepted_ids_count=len(accepted_ids),
                rejected_ids_count=len(rejected_ids),
            )
            return query_embedding, True

        # Compute centroid from liked-session embeddings
        liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
        if not liked_embeddings:
            raise EmbeddingSearchError(f"No embeddings found for liked sessions: {accepted_ids}")

        import numpy as np

        embedding_vectors = list(liked_embeddings.values())
        # Ensure all elements are pure Python floats (not numpy scalars)
        centroid_array = np.mean(embedding_vectors, axis=0)
        query_embedding = [float(x) for x in centroid_array]

        logger.info(
            "recommendation_centroid_computed",
            accepted_ids_count=len(accepted_ids),
            embeddings_found=len(liked_embeddings),
            rejected_ids_count=len(rejected_ids),
        )
        return query_embedding, False

    async def _recommend_fallback(
        self,
        db: Session,
        rejected_ids: list[int],
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
        limit: int = 10,
    ) -> list[tuple]:
        """
        Recommendation fallback using CRUD list_with_filters.

        Used when no semantic context (query, accepted_ids) is available.
        Efficiently filters using database queries, then excludes rejected IDs.

        Returns:
            List of tuples: (session, scores_dict) with basic metrics
        """
        try:
            # Use CRUD to fetch filtered sessions
            sessions = session_crud.list_with_filters(
                db=db,
                limit=limit + len(rejected_ids),  # Over-fetch to account for exclusions
                status=SessionStatus.PUBLISHED,
                event_id=event_id,
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                start_after=start_after,
                start_before=start_before,
                end_after=end_after,
                end_before=end_before,
            )

            # Exclude rejected sessions and compute scores
            recommendations = []
            for session in sessions:
                # Skip rejected sessions
                if session.id in rejected_ids:
                    continue

                # Compute basic scores (no semantic data available)
                scores = {
                    "overall_score": 1.0,  # All matches are equally valid without semantic scoring
                    "semantic_similarity": None,
                    "liked_cluster_similarity": None,
                    "disliked_similarity": None,
                    "filter_match_ratio": 1.0,  # All sessions passed CRUD filter
                    "explanation": "Matched all specified filters (no semantic query provided)",
                }

                recommendations.append((session, scores))

                if len(recommendations) >= limit:
                    break

            logger.info(
                "recommendation_crud_completed",
                rejected_ids_count=len(rejected_ids),
                recommendations=len(recommendations),
                limit=limit,
            )

            return recommendations

        except Exception as e:
            logger.error(
                "recommendation_crud_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmbeddingSearchError(f"CRUD recommendation fallback failed: {e!s}") from e

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """
        Compute cosine similarity between two vectors (0-1 range).

        Uses numpy for efficient computation. Maps cosine similarity from [-1, 1] to [0, 1].

        Args:
            vec1: First embedding vector
            vec2: Second embedding vector

        Returns:
            Cosine similarity score (0-1)
        """
        import numpy as np

        # Ensure vectors are numpy arrays and flatten to 1D
        v1 = np.asarray(vec1, dtype=np.float32).flatten()
        v2 = np.asarray(vec2, dtype=np.float32).flatten()

        # Compute cosine similarity: dot(v1, v2) / (||v1|| * ||v2||)
        dot_product = float(np.dot(v1, v2))
        norm_v1 = float(np.linalg.norm(v1))
        norm_v2 = float(np.linalg.norm(v2))

        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0

        cosine_sim = dot_product / (norm_v1 * norm_v2)
        # Map from [-1, 1] to [0, 1]: (sim + 1) / 2
        return float(max(0.0, (cosine_sim + 1.0) / 2.0))

    async def _compute_recommendation_scores(
        self,
        session_embedding: list[float],
        chroma_similarity: float,
        semantic_similarity_enabled: bool,
        accepted_ids: list[int],
        rejected_ids: list[int],
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
    ) -> dict:
        """
        Compute multi-dimensional recommendation scores with Phase 2 re-ranking.

        **Phase 2 Features:**
        - Actual centroid similarity: compute from liked session embeddings
        - Actual disliked penalty: compute max similarity to disliked embeddings
        - Re-ranking formula: weighted average that adapts to available scores
          - overall_score dynamically combines semantic_sim, liked_sim, disliked_sim
          - Result always in [0, 1] range with proper differentiation

        Args:
            session_embedding: Embedding vector for the result session
            chroma_similarity: Normalized similarity from Chroma (0-1)
            semantic_similarity_enabled: Whether query-based search was used
            accepted_ids: Liked session IDs
            rejected_ids: Disliked session IDs
            liked_embedding_weight: Weight (a) to boost liked-session similarity
            disliked_embedding_weight: Weight (b) to penalize disliked-session similarity

        Returns:
            Dict with overall_score, component scores, and explanation
        """
        # semantic_similarity: from Chroma (0-1, only if query-based)
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None

        # Phase 2: Compute actual centroid similarity from liked session embeddings
        liked_cluster_sim = None
        if accepted_ids:
            try:
                liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
                if liked_embeddings:
                    import numpy as np

                    # Compute centroid
                    centroid = np.mean(
                        list(liked_embeddings.values()),
                        axis=0,
                    ).tolist()
                    # Compute similarity to centroid
                    liked_cluster_sim = self._cosine_similarity(session_embedding, centroid)
            except Exception as e:
                logger.warning(
                    "liked_cluster_similarity_computation_failed",
                    error=str(e),
                    accepted_ids_count=len(accepted_ids),
                )

        # Phase 2: Compute actual disliked penalty from disliked session embeddings
        disliked_sim = None
        if rejected_ids:
            try:
                disliked_embeddings = await self.embedding_service.get_session_embeddings(
                    rejected_ids
                )
                if disliked_embeddings:
                    # Compute max similarity to any disliked session (the closest bad match)
                    disliked_sims = [
                        self._cosine_similarity(session_embedding, disliked_emb)
                        for disliked_emb in disliked_embeddings.values()
                    ]
                    disliked_sim = max(disliked_sims) if disliked_sims else None
            except Exception as e:
                logger.warning(
                    "disliked_similarity_computation_failed",
                    error=str(e),
                    rejected_ids_count=len(rejected_ids),
                )

        # filter_match_ratio: if filters were active, all matched sessions got them all
        filter_match_ratio = 1.0

        # Phase 2 Re-ranking formula: Dynamic weighted composition
        # Instead of additive (+) with clamping, use weighted average that adapts
        # to available scores. This ensures proper differentiation without 1.0 clumping.
        #
        # Strategy:
        # - Each score component contributes proportionally to its weight
        # - Disliked score inverts (1 - disliked_sim) so it can be combined properly
        # - Result: weighted_sum / total_weight, always in [0, 1]

        score_components = []
        score_weights = []
        explanation_parts = []

        # Add semantic similarity (always unit weight if available)
        if semantic_sim is not None:
            score_components.append(semantic_sim)
            score_weights.append(1.0)
            explanation_parts.append(f"semantic: {semantic_sim:.3f}")

        # Add liked cluster similarity with its weight
        if liked_cluster_sim is not None and liked_embedding_weight > 0:
            score_components.append(liked_cluster_sim)
            score_weights.append(liked_embedding_weight)
            explanation_parts.append(
                f"liked-cluster: {liked_cluster_sim:.3f} (weight: {liked_embedding_weight:.1f})"
            )

        # Add disliked similarity inverted (1 - disliked_sim) with its weight
        # This penalizes high disliked similarity (reduces overall score)
        if disliked_sim is not None and disliked_embedding_weight > 0:
            # Invert: high disliked_sim → low component contribution
            inverted_disliked = 1.0 - disliked_sim
            score_components.append(inverted_disliked)
            score_weights.append(disliked_embedding_weight)
            explanation_parts.append(
                f"disliked-penalty: {disliked_sim:.3f} (weight: {disliked_embedding_weight:.1f})"
            )

        # Calculate weighted average
        if score_components:
            import numpy as np

            weighted_sum = sum(c * w for c, w in zip(score_components, score_weights))
            total_weight = sum(score_weights)
            overall_score = weighted_sum / total_weight
        else:
            # No scoring data available, default to 0.5
            overall_score = 0.5

        # Ensure score is in valid range (should already be, but be safe)
        overall_score = max(0.0, min(1.0, overall_score))

        explanation = ", ".join(explanation_parts) if explanation_parts else "Matched filters"

        return {
            "overall_score": round(overall_score, 3),
            "semantic_similarity": round(semantic_sim, 3) if semantic_sim is not None else None,
            "liked_cluster_similarity": (
                round(liked_cluster_sim, 3) if liked_cluster_sim is not None else None
            ),
            "disliked_similarity": round(disliked_sim, 3) if disliked_sim is not None else None,
            "filter_match_ratio": filter_match_ratio,
            "explanation": explanation,
        }
