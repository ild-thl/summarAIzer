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
        filter_mode: str = "hard",
        filter_margin_weight: float = 0.1,
        soft_filter_limit_ratio: float = 0.5,
    ) -> list[tuple]:
        """
        Recommend sessions based on user preferences and optional filters.

        **Phase 2 Features:** Similarity-based re-ranking using embedding comparisons.
        Final score = base_score + a*liked_sim - b*disliked_sim

        **Phase 3 Features:** Soft filter margins expand candidate pools when hard filters
        are too restrictive, while maintaining transparency via compliance scoring.

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
            filter_mode: Phase 3 - "hard" (strict) or "soft" (margins), default "hard"
            filter_margin_weight: Phase 3 - weight to blend filter compliance into score (0-1, default 0.1)
            soft_filter_limit_ratio: Phase 3 - trigger soft pass if hard results < limit * ratio (0-1, default 0.5)

        Returns:
            List of tuples: (session, scores_dict) where scores_dict contains:
            - overall_score (0-1): Reranked score using Phase 2 formula + Phase 3 compliance
            - semantic_similarity (0-1 or None): Query similarity from Chroma
            - liked_cluster_similarity (0-1 or None): Centroid similarity (Phase 2)
            - disliked_similarity (0-1 or None): Max similarity to disliked sessions (Phase 2)
            - filter_match_ratio (0-1): Matching filters / total active filters
            - filter_compliance_score (0-1 or None): Phase 3 compliance (soft pass only)
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

            # Phase 3: Two-Pass Search Logic for Soft Filter Mode
            # ======================================================
            # Hard pass: Strict filter constraints applied
            nin_condition = {"session_id": {"$nin": list(seen_ids)}} if seen_ids else None
            chroma_where = self._combine_conditions(nin_condition, metadata_conditions)

            # Hard pass search
            try:
                if chroma_where:
                    chroma_results_hard = await self.embedding_service.search_similar_sessions(
                        query_embedding, limit=limit, where=chroma_where
                    )
                else:
                    chroma_results_hard = await self.embedding_service.search_similar_sessions(
                        query_embedding, limit=limit
                    )
            except Exception as e:
                logger.error(
                    "recommendation_chroma_search_failed",
                    error=str(e),
                )
                raise EmbeddingSearchError(f"Semantic search failed: {e!s}") from e

            # Soft pass: Check if we should expand with near-matches
            chroma_results_soft = []
            soft_pass_triggered = False
            soft_filter_threshold = limit * soft_filter_limit_ratio

            if filter_mode == "soft" and len(chroma_results_hard) < soft_filter_threshold:
                # Hard filters returned too few results, enable soft pass
                soft_pass_triggered = True
                try:
                    # Over-sample without filters (fetch 2x to account for filtering in _process_chroma_recommendations)
                    soft_search_limit = max(limit * 2, int(limit / soft_filter_limit_ratio))
                    # Search without metadata filters, still exclude seen sessions
                    chroma_where_soft = nin_condition  # Only exclude seen sessions

                    if chroma_where_soft:
                        chroma_results_soft = await self.embedding_service.search_similar_sessions(
                            query_embedding, limit=soft_search_limit, where=chroma_where_soft
                        )
                    else:
                        chroma_results_soft = await self.embedding_service.search_similar_sessions(
                            query_embedding, limit=soft_search_limit
                        )

                    logger.info(
                        "recommendation_soft_pass_enabled",
                        hard_results=len(chroma_results_hard),
                        soft_search_limit=soft_search_limit,
                        soft_results_before_dedup=len(chroma_results_soft),
                        soft_filter_threshold=soft_filter_threshold,
                    )
                except Exception as e:
                    logger.warning(
                        "recommendation_soft_pass_search_failed",
                        error=str(e),
                    )
                    # Soft pass failure is non-critical; proceed with hard results only
                    chroma_results_soft = []

            # Fetch from DB, apply filters, and compute scores with Phase 2 + Phase 3 re-ranking
            preference_embeddings = await self._prefetch_preference_embeddings(
                accepted_ids=accepted_ids,
                rejected_ids=rejected_ids,
            )
            recommendations = await self._process_chroma_recommendations(
                chroma_results_hard=chroma_results_hard,
                chroma_results_soft=chroma_results_soft,
                db=db,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=preference_embeddings["liked"],
                disliked_embeddings=preference_embeddings["disliked"],
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
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
                filter_margin_weight=filter_margin_weight,
                limit=limit,
                soft_pass_triggered=soft_pass_triggered,
            )

            logger.info(
                "recommendation_completed",
                hard_pass_results=len(chroma_results_hard),
                soft_pass_results=len(chroma_results_soft),
                final_recommendations=len(recommendations),
                limit=limit,
                soft_pass_triggered=soft_pass_triggered,
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
        chroma_results_hard: list,
        chroma_results_soft: list,
        db: Session,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
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
        filter_margin_weight: float,
        limit: int,
        soft_pass_triggered: bool = False,
    ) -> list[tuple]:
        """Process Chroma search results into recommendations with Phase 2 & 3 re-ranking.

        Handles both hard-pass and soft-pass results:
        - Hard pass: Results strictly matching all filters
        - Soft pass: Near-matches when hard filters are too restrictive (Phase 3)

        Blends Phase 2 re-ranking (semantic + liked/disliked) with Phase 3 compliance scoring.

        Args:
            chroma_results_hard: Strict filter-matched results from hard pass
            chroma_results_soft: Near-match results from soft pass (if triggered)
            db: Database session
            semantic_similarity_enabled: Whether query-based search was used
            liked_embeddings: Prefetched liked session embeddings keyed by session ID
            disliked_embeddings: Prefetched disliked session embeddings keyed by session ID
            liked_embedding_weight: Weight to boost liked-session similarities
            disliked_embedding_weight: Weight to penalize disliked-session similarities
            event_id: Optional event filter
            session_format: Optional format filter
            tags: Optional tags filter
            location: Optional location filter
            language: Optional language filter
            duration_min: Optional minimum duration
            duration_max: Optional maximum duration
            start_after: Optional start datetime lower bound
            start_before: Optional start datetime upper bound
            end_after: Optional end datetime lower bound
            end_before: Optional end datetime upper bound
            filter_mode: "hard" (strict) or "soft" (margins)
            filter_margin_weight: Weight to blend compliance into score (Phase 3)
            limit: Max recommendations to return
            soft_pass_triggered: Whether soft pass was executed

        Returns:
            List of tuples: (session, scores_dict) with Phase 2 & 3 scores
        """
        # Collect embeddings and set up data structures
        chroma_id_to_embedding = await self._batch_fetch_embeddings(
            chroma_results_hard, chroma_results_soft
        )
        hard_pass_session_ids = {session_id for session_id, _, _ in chroma_results_hard}
        recommendations = []

        # Process hard pass results (strict filter matches)
        await self._process_hard_pass_results(
            chroma_results_hard=chroma_results_hard,
            db=db,
            event_id=event_id,
            chroma_id_to_embedding=chroma_id_to_embedding,
            semantic_similarity_enabled=semantic_similarity_enabled,
            liked_embeddings=liked_embeddings,
            disliked_embeddings=disliked_embeddings,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_margin_weight=filter_margin_weight,
            limit=limit,
            recommendations=recommendations,
        )

        # Process soft pass results (near-matches) if triggered
        if soft_pass_triggered and chroma_results_soft:
            await self._process_soft_pass_results(
                chroma_results_soft=chroma_results_soft,
                hard_pass_session_ids=hard_pass_session_ids,
                db=db,
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
                chroma_id_to_embedding=chroma_id_to_embedding,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_margin_weight=filter_margin_weight,
                limit=limit,
                recommendations=recommendations,
            )

        # Extract session and scores (removing source tag)
        recommendations_final = [(session, scores) for session, scores, _ in recommendations]

        # Sort by overall_score (highest first) - this is the re-ranked score
        recommendations_final.sort(key=lambda x: x[1]["overall_score"], reverse=True)

        # Return top limit results
        recommendations_final = recommendations_final[:limit]

        logger.info(
            "recommendation_completed",
            hard_pass_results=sum(1 for _, _, src in recommendations if src == "hard"),
            soft_pass_results=sum(1 for _, _, src in recommendations if src == "soft"),
            final_recommendations=len(recommendations_final),
            limit=limit,
            soft_pass_triggered=soft_pass_triggered,
        )

        return recommendations_final

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
                    "filter_compliance_score": None,  # Not computed for CRUD fallback
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

    async def _prefetch_preference_embeddings(
        self,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> dict[str, dict[int, list[float]]]:
        """Fetch liked/disliked embeddings once per request for reuse during scoring."""
        liked_embeddings: dict[int, list[float]] = {}
        disliked_embeddings: dict[int, list[float]] = {}

        if accepted_ids:
            try:
                liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
            except Exception as e:
                logger.warning(
                    "liked_embeddings_prefetch_failed",
                    error=str(e),
                    accepted_ids_count=len(accepted_ids),
                )

        if rejected_ids:
            try:
                disliked_embeddings = await self.embedding_service.get_session_embeddings(
                    rejected_ids
                )
            except Exception as e:
                logger.warning(
                    "disliked_embeddings_prefetch_failed",
                    error=str(e),
                    rejected_ids_count=len(rejected_ids),
                )

        return {
            "liked": liked_embeddings,
            "disliked": disliked_embeddings,
        }

    def _get_default_embedding(self) -> list[float]:
        """Build a zero-vector fallback matching configured embedding dimensions."""
        return [0.0] * self.embedding_service.embedding_dimension

    async def _batch_fetch_embeddings(
        self,
        chroma_results_hard: list,
        chroma_results_soft: list,
    ) -> dict:
        """Batch fetch embeddings for all session IDs from hard and soft results."""
        all_session_ids = []
        for session_id, _, _ in chroma_results_hard:
            all_session_ids.append(session_id)
        for session_id, _, _ in chroma_results_soft:
            all_session_ids.append(session_id)

        chroma_id_to_embedding = {}
        if all_session_ids:
            try:
                embeddings_dict = await self.embedding_service.get_session_embeddings(
                    all_session_ids
                )
                for session_id, embedding in embeddings_dict.items():
                    chroma_id_to_embedding[f"session_{session_id}"] = embedding
            except Exception as e:
                logger.warning(
                    "batch_embedding_retrieval_failed",
                    error=str(e),
                    sessions_count=len(all_session_ids),
                )
        return chroma_id_to_embedding

    async def _process_hard_pass_results(
        self,
        chroma_results_hard: list,
        db: Session,
        event_id: int | None,
        chroma_id_to_embedding: dict,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_margin_weight: float,
        limit: int,
        recommendations: list,
    ) -> None:
        """Process hard pass results (strict filter matches)."""
        for session_id, chroma_similarity, _ in chroma_results_hard:
            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue

            if event_id and session.event_id != event_id:
                continue

            chroma_id = f"session_{session_id}"
            session_embedding = chroma_id_to_embedding.get(chroma_id)

            if session_embedding is None:
                logger.warning("session_embedding_not_found", session_id=session_id)
                session_embedding = self._get_default_embedding()

            filter_compliance_score = 1.0

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_compliance_score=filter_compliance_score,
                filter_margin_weight=filter_margin_weight,
            )

            recommendations.append((session, scores, "hard"))

            if len(recommendations) >= limit:
                break

    async def _process_soft_pass_results(
        self,
        chroma_results_soft: list,
        hard_pass_session_ids: set,
        db: Session,
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
        chroma_id_to_embedding: dict,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_margin_weight: float,
        limit: int,
        recommendations: list,
    ) -> None:
        """Process soft pass results (near-matches)."""
        for session_id, chroma_similarity, _ in chroma_results_soft:
            if session_id in hard_pass_session_ids:
                continue

            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue

            if event_id and session.event_id != event_id:
                continue

            if any(rec[0].id == session_id for rec in recommendations):
                continue

            chroma_id = f"session_{session_id}"
            session_embedding = chroma_id_to_embedding.get(chroma_id)

            if session_embedding is None:
                logger.warning("session_embedding_not_found_soft_pass", session_id=session_id)
                session_embedding = self._get_default_embedding()

            filter_compliance_score = self._compute_filter_compliance_score(
                session=session,
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

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_compliance_score=filter_compliance_score,
                filter_margin_weight=filter_margin_weight,
            )

            recommendations.append((session, scores, "soft"))

            if len(recommendations) >= limit:
                break

    def _check_format(self, session, session_format: str | None) -> bool:
        """Check if session format matches filter."""
        if session_format is None:
            return False
        return session.session_format and session.session_format.value == session_format

    def _check_language(self, session, language: str | None) -> bool:
        """Check if session language matches filter."""
        if language is None:
            return False
        return session.language == language

    def _check_tags(self, session, tags: list[str] | None) -> bool:
        """Check if session has matching tags (OR logic)."""
        if not tags:
            return False
        session_tags_set = set(session.tags or [])
        return any(tag in session_tags_set for tag in tags)

    def _check_location(self, session, location: list[str] | None) -> bool:
        """Check if session location matches (OR logic)."""
        if not location:
            return False
        return session.location and any(loc in session.location for loc in location)

    def _check_duration_min(self, session, duration_min: int | None) -> bool:
        """Check if session meets minimum duration."""
        if duration_min is None:
            return False
        return session.duration and session.duration >= duration_min

    def _check_duration_max(self, session, duration_max: int | None) -> bool:
        """Check if session stays within maximum duration."""
        if duration_max is None:
            return False
        return session.duration and session.duration <= duration_max

    def _check_start_after(self, session, start_after: Any | None) -> bool:
        """Check if session starts after datetime."""
        if start_after is None:
            return False
        return session.start_datetime >= start_after

    def _check_start_before(self, session, start_before: Any | None) -> bool:
        """Check if session starts before datetime."""
        if start_before is None:
            return False
        return session.start_datetime <= start_before

    def _check_end_after(self, session, end_after: Any | None) -> bool:
        """Check if session ends after datetime."""
        if end_after is None:
            return False
        return session.end_datetime >= end_after

    def _check_end_before(self, session, end_before: Any | None) -> bool:
        """Check if session ends before datetime."""
        if end_before is None:
            return False
        return session.end_datetime <= end_before

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

    def _compute_filter_compliance_score(
        self,
        session,
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
    ) -> float:
        """
        Compute filter compliance score for a session (0-1).

        Calculates what fraction of active filters the session matches.
        Used in Phase 3 (soft filter margins) to score near-matches.

        Example: With `language=en`, `format=workshop`, `duration_max=60`:
        - Session A: English + workshop + 45min  → 3/3 = 1.0 (perfect)
        - Session B: English + talk + 50min      → 1/3 = 0.33 (language only)

        Args:
            session: Session model instance
            session_format: Format to check
            tags: Tags to check (matches if any tag in session matches any in list)
            location: Locations to check (matches if any location matches)
            language: Language to check
            duration_min/max: Duration bounds to check
            start_after/before, end_after/before: Datetime bounds to check

        Returns:
            Compliance score (0-1), where 1.0 means all filters matched
        """
        filter_checks = [
            (session_format is not None, self._check_format(session, session_format)),
            (language is not None, self._check_language(session, language)),
            (tags is not None, self._check_tags(session, tags)),
            (location is not None, self._check_location(session, location)),
            (duration_min is not None, self._check_duration_min(session, duration_min)),
            (duration_max is not None, self._check_duration_max(session, duration_max)),
            (start_after is not None, self._check_start_after(session, start_after)),
            (start_before is not None, self._check_start_before(session, start_before)),
            (end_after is not None, self._check_end_after(session, end_after)),
            (end_before is not None, self._check_end_before(session, end_before)),
        ]

        matched = sum(check for is_active, check in filter_checks if is_active)
        total = sum(1 for is_active, _ in filter_checks if is_active)

        return 1.0 if total == 0 else matched / total

    def _compute_liked_similarity(
        self,
        session_embedding: list[float],
        liked_embeddings: dict[int, list[float]],
    ) -> float | None:
        """Compute centroid similarity from prefetched liked session embeddings."""
        if not liked_embeddings:
            return None

        try:
            import numpy as np

            centroid = np.mean(list(liked_embeddings.values()), axis=0).tolist()
            return self._cosine_similarity(session_embedding, centroid)
        except Exception as e:
            logger.warning(
                "liked_cluster_similarity_computation_failed",
                error=str(e),
                embeddings_count=len(liked_embeddings),
            )
            return None

    def _compute_disliked_similarity(
        self,
        session_embedding: list[float],
        disliked_embeddings: dict[int, list[float]],
    ) -> float | None:
        """Compute max similarity to prefetched disliked session embeddings."""
        if not disliked_embeddings:
            return None

        try:
            disliked_sims = [
                self._cosine_similarity(session_embedding, disliked_emb)
                for disliked_emb in disliked_embeddings.values()
            ]
            return max(disliked_sims) if disliked_sims else None
        except Exception as e:
            logger.warning(
                "disliked_similarity_computation_failed",
                error=str(e),
                embeddings_count=len(disliked_embeddings),
            )
            return None

    def _build_score_components(
        self,
        semantic_sim: float | None,
        liked_cluster_sim: float | None,
        disliked_sim: float | None,
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_compliance_score: float | None,
        filter_margin_weight: float,
    ) -> tuple[list, list, list]:
        """Build score components, weights, and explanation parts."""
        components = []
        weights = []
        explanations = []

        if semantic_sim is not None:
            components.append(semantic_sim)
            weights.append(1.0)
            explanations.append(f"semantic: {semantic_sim:.3f}")

        if liked_cluster_sim is not None and liked_embedding_weight > 0:
            components.append(liked_cluster_sim)
            weights.append(liked_embedding_weight)
            explanations.append(
                f"liked-cluster: {liked_cluster_sim:.3f} (weight: {liked_embedding_weight:.1f})"
            )

        if disliked_sim is not None and disliked_embedding_weight > 0:
            inverted_disliked = 1.0 - disliked_sim
            components.append(inverted_disliked)
            weights.append(disliked_embedding_weight)
            explanations.append(
                f"disliked-penalty: {disliked_sim:.3f} (weight: {disliked_embedding_weight:.1f})"
            )

        if filter_compliance_score is not None and filter_margin_weight > 0:
            components.append(filter_compliance_score)
            weights.append(filter_margin_weight)
            explanations.append(
                f"filter-compliance: {filter_compliance_score:.3f} (weight: {filter_margin_weight:.1f})"
            )

        return components, weights, explanations

    @staticmethod
    def _calculate_overall_score(components: list, weights: list) -> float:
        """Calculate overall score from components and weights."""
        if not components:
            return 0.5

        weighted_sum = sum(c * w for c, w in zip(components, weights, strict=False))
        total_weight = sum(weights)
        overall_score = weighted_sum / total_weight if total_weight > 0 else 0.5

        return max(0.0, min(1.0, overall_score))

    async def _compute_recommendation_scores(
        self,
        session_embedding: list[float],
        chroma_similarity: float,
        semantic_similarity_enabled: bool,
        liked_embeddings: dict[int, list[float]],
        disliked_embeddings: dict[int, list[float]],
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
        filter_compliance_score: float | None = None,
        filter_margin_weight: float = 0.1,
    ) -> dict:
        """
        Compute multi-dimensional recommendation scores with Phase 2 & 3 features.

        **Phase 2 Features:**
        - Actual centroid similarity: compute from liked session embeddings
        - Actual disliked penalty: compute max similarity to disliked embeddings
        - Re-ranking formula: weighted average that adapts to available scores
          - overall_score dynamically combines semantic_sim, liked_sim, disliked_sim
          - Result always in [0, 1] range with proper differentiation

        **Phase 3 Features:**
        - Filter compliance scoring: how well session matches requested filters
        - Soft filter margins: blend compliance into overall score to expand candidates
          - filter_margin_weight controls compliance contribution
          - Enables near-matches to rank higher than hard cutoff

        Args:
            session_embedding: Embedding vector for the result session
            chroma_similarity: Normalized similarity from Chroma (0-1)
            semantic_similarity_enabled: Whether query-based search was used
            liked_embeddings: Prefetched liked session embeddings
            disliked_embeddings: Prefetched disliked session embeddings
            liked_embedding_weight: Weight (a) to boost liked-session similarity
            disliked_embedding_weight: Weight (b) to penalize disliked-session similarity
            filter_compliance_score: Phase 3 - ratio of matched filters (0-1)
            filter_margin_weight: Phase 3 - weight to blend compliance into score

        Returns:
            Dict with overall_score, component scores, and explanation
        """
        # Compute component scores
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None
        liked_cluster_sim = self._compute_liked_similarity(session_embedding, liked_embeddings)
        disliked_sim = self._compute_disliked_similarity(session_embedding, disliked_embeddings)

        # Build score components and weights
        score_components, score_weights, explanation_parts = self._build_score_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_compliance_score=filter_compliance_score,
            filter_margin_weight=filter_margin_weight,
        )

        # Calculate overall score
        overall_score = self._calculate_overall_score(score_components, score_weights)

        explanation = ", ".join(explanation_parts) if explanation_parts else "Matched filters"

        return {
            "overall_score": round(overall_score, 3),
            "semantic_similarity": round(semantic_sim, 3) if semantic_sim is not None else None,
            "liked_cluster_similarity": (
                round(liked_cluster_sim, 3) if liked_cluster_sim is not None else None
            ),
            "disliked_similarity": round(disliked_sim, 3) if disliked_sim is not None else None,
            "filter_match_ratio": 1.0,
            "filter_compliance_score": (
                round(filter_compliance_score, 3) if filter_compliance_score is not None else None
            ),
            "explanation": explanation,
        }
