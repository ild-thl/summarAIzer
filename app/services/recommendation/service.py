"""Recommendation orchestration service.

Keeps recommendation flow and ranking logic isolated from search-only services.
"""

from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.services.embedding.exceptions import EmbeddingSearchError, InvalidEmbeddingTextError
from app.services.embedding.service import EmbeddingService
from app.services.recommendation.filters import RecommendationFilterEvaluator
from app.services.recommendation.planning import RecommendationPlanner
from app.services.recommendation.scoring import RecommendationScoreEngine

logger = structlog.get_logger()


class RecommendationService:
    """Coordinates recommendation execution paths and filter-mode semantic search."""

    def __init__(self, embedding_service: EmbeddingService):
        self.embedding_service = embedding_service
        self.recommendation_planner = RecommendationPlanner()
        self.filter_evaluator = RecommendationFilterEvaluator(self.recommendation_planner)
        self.score_engine = RecommendationScoreEngine()

    @staticmethod
    def _extract_window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
        """Extract (start, end) from TimeWindow objects or plain dicts."""
        if isinstance(window, dict):
            return window.get("start"), window.get("end")
        return getattr(window, "start", None), getattr(window, "end", None)

    @staticmethod
    def _combine_conditions(condition1: dict | None, condition2: dict | None) -> dict | None:
        if not condition1 and not condition2:
            return None
        if not condition1:
            return condition2
        if not condition2:
            return condition1
        return {"$and": [condition1, condition2]}

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

    def _apply_plan_mode_if_needed(
        self,
        recommendations: list[tuple],
        is_plan_mode: bool,
        limit: int,
        time_windows: list[Any] | None,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> list[tuple]:
        if not is_plan_mode:
            return recommendations
        return self._optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
        )

    def _optimize_session_plan(
        self,
        recommendations: list[tuple],
        limit: int,
        time_windows: list[Any] | None,
        min_break_minutes: int,
        max_gap_minutes: int | None,
    ) -> list[tuple]:
        return self.recommendation_planner.optimize_session_plan(
            recommendations=recommendations,
            limit=limit,
            time_windows=time_windows,
            min_break_minutes=min_break_minutes,
            max_gap_minutes=max_gap_minutes,
        )

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
        liked_embedding_weight: float = 0.3,
        disliked_embedding_weight: float = 0.2,
        filter_mode: str = "hard",
        filter_margin_weight: float = 0.1,
        goal_mode: str = "similarity",
        time_windows: list[Any] | None = None,
        min_break_minutes: int = 0,
        max_gap_minutes: int | None = None,
        plan_candidate_multiplier: int = 3,
    ) -> list[tuple]:
        accepted_ids = accepted_ids or []
        rejected_ids = rejected_ids or []
        seen_ids = set(accepted_ids + rejected_ids)
        is_plan_mode = goal_mode == "plan"
        candidate_limit = limit * plan_candidate_multiplier if is_plan_mode else limit

        try:
            if not query and not accepted_ids:
                logger.info(
                    "recommendation_crud_fallback",
                    rejected_ids_count=len(rejected_ids),
                    goal_mode=goal_mode,
                )
                recommendations = await self._recommend_fallback(
                    db,
                    rejected_ids=rejected_ids,
                    event_id=event_id,
                    session_format=session_format,
                    tags=tags,
                    location=location,
                    language=language,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    time_windows=time_windows,
                    filter_mode=filter_mode,
                    filter_margin_weight=filter_margin_weight,
                    limit=candidate_limit,
                )
                return self._apply_plan_mode_if_needed(
                    recommendations=recommendations,
                    is_plan_mode=is_plan_mode,
                    limit=limit,
                    time_windows=time_windows,
                    min_break_minutes=min_break_minutes,
                    max_gap_minutes=max_gap_minutes,
                )

            recommendations, search_debug = await self._recommend_with_semantic_search(
                db=db,
                query=query,
                accepted_ids=accepted_ids,
                rejected_ids=rejected_ids,
                seen_ids=seen_ids,
                candidate_limit=candidate_limit,
                event_id=event_id,
                session_format=session_format,
                tags=tags,
                location=location,
                language=language,
                duration_min=duration_min,
                duration_max=duration_max,
                time_windows=time_windows,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_mode=filter_mode,
                filter_margin_weight=filter_margin_weight,
            )
            recommendations = self._apply_plan_mode_if_needed(
                recommendations=recommendations,
                is_plan_mode=is_plan_mode,
                limit=limit,
                time_windows=time_windows,
                min_break_minutes=min_break_minutes,
                max_gap_minutes=max_gap_minutes,
            )

            logger.info(
                "recommendation_completed",
                hard_pass_results=search_debug["hard_pass_results"],
                soft_pass_results=search_debug["soft_pass_results"],
                final_recommendations=len(recommendations),
                limit=limit,
                soft_pass_triggered=search_debug["soft_pass_triggered"],
                goal_mode=goal_mode,
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

    async def _recommend_with_semantic_search(
        self,
        db: Session,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
        seen_ids: set[int],
        candidate_limit: int,
        event_id: int | None,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
        liked_embedding_weight: float,
        disliked_embedding_weight: float,
        filter_mode: str,
        filter_margin_weight: float,
    ) -> tuple[list[tuple], dict[str, Any]]:
        query_embedding, semantic_similarity_enabled = await self._determine_query_embedding(
            query=query,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
        )

        metadata_conditions = self._build_chroma_conditions(
            session_format=session_format,
            tags=tags,
            location=location,
            language=language,
            duration_min=duration_min,
            duration_max=duration_max,
        )
        time_windows_condition = self._build_time_windows_conditions(time_windows)
        metadata_conditions = self._combine_conditions(metadata_conditions, time_windows_condition)

        nin_condition = {"session_id": {"$nin": list(seen_ids)}} if seen_ids else None
        chroma_where = self._combine_conditions(nin_condition, metadata_conditions)

        chroma_results_soft = []
        soft_pass_triggered = False

        if filter_mode == "soft":
            soft_pass_triggered = True
            chroma_results_hard = []
            try:
                soft_search_limit = candidate_limit * 2
                chroma_results_soft = await self._collect_soft_pass_candidates(
                    query_embedding=query_embedding,
                    soft_search_limit=soft_search_limit,
                    nin_condition=nin_condition,
                    time_windows=time_windows,
                )
                logger.info(
                    "recommendation_soft_mode_direct_pass",
                    soft_search_limit=soft_search_limit,
                    soft_results_before_dedup=len(chroma_results_soft),
                )
            except Exception as e:
                logger.warning("recommendation_soft_pass_search_failed", error=str(e))
                chroma_results_soft = []
        else:
            try:
                if chroma_where:
                    chroma_results_hard = await self.embedding_service.search_similar_sessions(
                        query_embedding, limit=candidate_limit, where=chroma_where
                    )
                else:
                    chroma_results_hard = await self.embedding_service.search_similar_sessions(
                        query_embedding, limit=candidate_limit
                    )
            except Exception as e:
                logger.error("recommendation_chroma_search_failed", error=str(e))
                raise EmbeddingSearchError(f"Semantic search failed: {e!s}") from e

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
            time_windows=time_windows,
            filter_margin_weight=filter_margin_weight,
            limit=candidate_limit,
            soft_pass_triggered=soft_pass_triggered,
        )

        return recommendations, {
            "hard_pass_results": len(chroma_results_hard),
            "soft_pass_results": len(chroma_results_soft),
            "soft_pass_triggered": soft_pass_triggered,
        }

    async def _collect_soft_pass_candidates(
        self,
        query_embedding: list[float],
        soft_search_limit: int,
        nin_condition: dict | None,
        time_windows: list[Any] | None,
    ) -> list:
        if not time_windows:
            chroma_where_soft = self._combine_conditions(nin_condition, None)
            if chroma_where_soft:
                return await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=soft_search_limit,
                    where=chroma_where_soft,
                )
            return await self.embedding_service.search_similar_sessions(
                query_embedding,
                limit=soft_search_limit,
            )

        per_window_limit = max(1, soft_search_limit // len(time_windows))
        collected_results = []
        for window in time_windows:
            window_condition = self._build_time_windows_conditions([window])
            chroma_where_soft = self._combine_conditions(nin_condition, window_condition)
            if chroma_where_soft:
                results = await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=per_window_limit,
                    where=chroma_where_soft,
                )
            else:
                results = await self.embedding_service.search_similar_sessions(
                    query_embedding,
                    limit=per_window_limit,
                )
            collected_results.extend(results)

        deduped: dict[int, tuple[int, float, Any]] = {}
        for session_id, similarity, metadata in collected_results:
            current = deduped.get(session_id)
            if current is None or similarity > current[1]:
                deduped[session_id] = (session_id, similarity, metadata)

        deduped_results = list(deduped.values())
        deduped_results.sort(key=lambda item: item[1], reverse=True)
        return deduped_results[:soft_search_limit]

    async def _determine_query_embedding(
        self,
        query: str | None,
        accepted_ids: list[int],
        rejected_ids: list[int],
    ) -> tuple[list, bool]:
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

        liked_embeddings = await self.embedding_service.get_session_embeddings(accepted_ids)
        if not liked_embeddings:
            raise EmbeddingSearchError(f"No embeddings found for liked sessions: {accepted_ids}")

        import numpy as np

        centroid_array = np.mean(list(liked_embeddings.values()), axis=0)
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
        time_windows: list[Any] | None = None,
        filter_mode: str = "hard",
        filter_margin_weight: float = 0.1,
        limit: int = 10,
    ) -> list[tuple]:
        try:
            if filter_mode == "soft":
                # In soft mode fallback we expand candidates first, then rank by compliance.
                sessions = session_crud.list_with_filters(
                    db=db,
                    limit=(limit * 2) + len(rejected_ids),
                    status=SessionStatus.PUBLISHED,
                    event_id=event_id,
                    time_windows=time_windows,
                )
            else:
                sessions = session_crud.list_with_filters(
                    db=db,
                    limit=limit + len(rejected_ids),
                    status=SessionStatus.PUBLISHED,
                    event_id=event_id,
                    session_format=session_format,
                    tags=tags,
                    location=location,
                    language=language,
                    duration_min=duration_min,
                    duration_max=duration_max,
                    time_windows=time_windows,
                )

            recommendations = []
            for session in sessions:
                if session.id in rejected_ids:
                    continue

                if filter_mode == "soft":
                    filter_compliance_score = self._compute_filter_compliance_score(
                        session=session,
                        session_format=session_format,
                        tags=tags,
                        location=location,
                        language=language,
                        duration_min=duration_min,
                        duration_max=duration_max,
                        time_windows=time_windows,
                    )
                    scores = await self._compute_recommendation_scores(
                        session_embedding=self._get_default_embedding(),
                        chroma_similarity=0.0,
                        semantic_similarity_enabled=False,
                        liked_embeddings={},
                        disliked_embeddings={},
                        liked_embedding_weight=0.0,
                        disliked_embedding_weight=0.0,
                        filter_compliance_score=filter_compliance_score,
                        filter_margin_weight=filter_margin_weight,
                    )
                else:
                    scores = {
                        "overall_score": 1.0,
                        "semantic_similarity": None,
                        "liked_cluster_similarity": None,
                        "disliked_similarity": None,
                        "filter_compliance_score": None,
                    }
                recommendations.append((session, scores))

            if filter_mode == "soft":
                recommendations.sort(key=lambda item: item[1]["overall_score"], reverse=True)

            recommendations = recommendations[:limit]

            logger.info(
                "recommendation_crud_completed",
                rejected_ids_count=len(rejected_ids),
                recommendations=len(recommendations),
                limit=limit,
                filter_mode=filter_mode,
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

        return {"liked": liked_embeddings, "disliked": disliked_embeddings}

    def _get_default_embedding(self) -> list[float]:
        return [0.0] * self.embedding_service.embedding_dimension

    async def _batch_fetch_embeddings(
        self,
        chroma_results_hard: list,
        chroma_results_soft: list,
    ) -> dict:
        # Keep ordering stable but remove duplicates to satisfy Chroma get() constraints.
        all_session_ids = list(
            dict.fromkeys(
                [session_id for session_id, _, _ in chroma_results_hard]
                + [session_id for session_id, _, _ in chroma_results_soft]
            )
        )

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
        time_windows: list[Any] | None,
        filter_margin_weight: float,
        limit: int,
        soft_pass_triggered: bool = False,
    ) -> list[tuple]:
        chroma_id_to_embedding = await self._batch_fetch_embeddings(
            chroma_results_hard, chroma_results_soft
        )
        hard_pass_session_ids = {session_id for session_id, _, _ in chroma_results_hard}
        recommendations = []

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
                time_windows=time_windows,
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

        recommendations_final = [(session, scores) for session, scores, _ in recommendations]
        recommendations_final.sort(key=lambda x: x[1]["overall_score"], reverse=True)
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
        for session_id, chroma_similarity, _ in chroma_results_hard:
            session = session_crud.read(db, session_id)
            if not session or session.status != SessionStatus.PUBLISHED:
                continue
            if event_id and session.event_id != event_id:
                continue

            session_embedding = chroma_id_to_embedding.get(f"session_{session_id}")
            if session_embedding is None:
                logger.warning("session_embedding_not_found", session_id=session_id)
                session_embedding = self._get_default_embedding()

            scores = await self._compute_recommendation_scores(
                session_embedding=session_embedding,
                chroma_similarity=chroma_similarity,
                semantic_similarity_enabled=semantic_similarity_enabled,
                liked_embeddings=liked_embeddings,
                disliked_embeddings=disliked_embeddings,
                liked_embedding_weight=liked_embedding_weight,
                disliked_embedding_weight=disliked_embedding_weight,
                filter_compliance_score=1.0,
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
        time_windows: list[Any] | None,
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

            session_embedding = chroma_id_to_embedding.get(f"session_{session_id}")
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
                time_windows=time_windows,
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

    def _compute_filter_compliance_score(
        self,
        session,
        session_format: str | None,
        tags: list[str] | None,
        location: list[str] | None,
        language: str | None,
        duration_min: int | None,
        duration_max: int | None,
        time_windows: list[Any] | None,
    ) -> float:
        return self.filter_evaluator.compute_filter_compliance_score(
            session=session,
            session_format=session_format,
            tags=tags,
            location=location,
            language=language,
            duration_min=duration_min,
            duration_max=duration_max,
            time_windows=time_windows,
        )

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        import numpy as np

        v1 = np.asarray(vec1, dtype=np.float32).flatten()
        v2 = np.asarray(vec2, dtype=np.float32).flatten()

        dot_product = float(np.dot(v1, v2))
        norm_v1 = float(np.linalg.norm(v1))
        norm_v2 = float(np.linalg.norm(v2))

        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0

        cosine_sim = dot_product / (norm_v1 * norm_v2)
        return float(max(0.0, (cosine_sim + 1.0) / 2.0))

    def _compute_liked_similarity(
        self,
        session_embedding: list[float],
        liked_embeddings: dict[int, list[float]],
    ) -> float | None:
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
        strategy_weights: dict[str, float] = {
            "liked": liked_embedding_weight,
            "disliked": disliked_embedding_weight,
            "compliance": filter_margin_weight,
        }
        return self.score_engine.build_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            filter_compliance_score=filter_compliance_score,
            weights=strategy_weights,
        )

    @staticmethod
    def _calculate_overall_score(components: list, weights: list) -> float:
        return RecommendationScoreEngine.calculate_overall_score(components, weights)

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
        semantic_sim = chroma_similarity if semantic_similarity_enabled else None
        liked_cluster_sim = self._compute_liked_similarity(session_embedding, liked_embeddings)
        disliked_sim = self._compute_disliked_similarity(session_embedding, disliked_embeddings)

        score_components, score_weights = self._build_score_components(
            semantic_sim=semantic_sim,
            liked_cluster_sim=liked_cluster_sim,
            disliked_sim=disliked_sim,
            liked_embedding_weight=liked_embedding_weight,
            disliked_embedding_weight=disliked_embedding_weight,
            filter_compliance_score=filter_compliance_score,
            filter_margin_weight=filter_margin_weight,
        )
        overall_score = self._calculate_overall_score(score_components, score_weights)

        return {
            "overall_score": round(overall_score, 3),
            "semantic_similarity": round(semantic_sim, 3) if semantic_sim is not None else None,
            "liked_cluster_similarity": (
                round(liked_cluster_sim, 3) if liked_cluster_sim is not None else None
            ),
            "disliked_similarity": round(disliked_sim, 3) if disliked_sim is not None else None,
            "filter_compliance_score": (
                round(filter_compliance_score, 3) if filter_compliance_score is not None else None
            ),
        }
