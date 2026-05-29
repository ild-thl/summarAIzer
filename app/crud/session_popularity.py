"""CRUD operations for SessionPopularity model."""

import math
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import Session as SessionModel
from app.database.models import SessionPopularity

logger = structlog.get_logger()


class CRUDSessionPopularity:
    """Upsert-based CRUD for session acceptance/rejection counters."""

    def record_interactions(
        self,
        db: Session,
        accepted_ids: list[int],
        rejected_ids: list[int],
        event_id: int | None,
    ) -> None:
        """Increment acceptance and rejection counters for the given session IDs.

        Accepts overcounting (same session appearing across multiple requests is
        counted each time — this is intentional for now).
        """
        now = datetime.utcnow()

        try:
            requested_ids = list(dict.fromkeys([*accepted_ids, *rejected_ids]))
            if not requested_ids:
                return

            existing_sessions_query = db.query(SessionModel.id).filter(
                SessionModel.id.in_(requested_ids)
            )
            if event_id is not None:
                existing_sessions_query = existing_sessions_query.filter(
                    SessionModel.event_id == event_id
                )

            existing_ids = {row[0] for row in existing_sessions_query.all()}

            for session_id, field in (
                *((sid, "acceptance_count") for sid in accepted_ids if sid in existing_ids),
                *((sid, "rejection_count") for sid in rejected_ids if sid in existing_ids),
            ):
                row = (
                    db.query(SessionPopularity)
                    .filter(
                        SessionPopularity.session_id == session_id,
                        SessionPopularity.event_id == event_id,
                    )
                    .first()
                )
                if row is None:
                    row = SessionPopularity(
                        session_id=session_id,
                        event_id=event_id,
                        acceptance_count=0,
                        rejection_count=0,
                        updated_at=now,
                    )
                    db.add(row)
                    db.flush()  # make row visible within this transaction

                setattr(row, field, getattr(row, field) + 1)
                row.updated_at = now

            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("popularity_record_failed", error=str(e), error_type=type(e).__name__)

    def get_popularity_map(
        self,
        db: Session,
        session_ids: list[int],
        event_id: int | None,
    ) -> dict[int, dict[str, Any]]:
        """Return raw counts for the given session IDs scoped to the event.

        Returns a dict keyed by session_id with keys:
          acceptance_count, rejection_count
        Missing sessions are not included (count is implicitly 0).
        """
        if not session_ids:
            return {}

        rows = (
            db.query(SessionPopularity)
            .filter(
                SessionPopularity.session_id.in_(session_ids),
                SessionPopularity.event_id == event_id,
            )
            .all()
        )
        return {
            row.session_id: {
                "acceptance_count": row.acceptance_count,
                "rejection_count": row.rejection_count,
            }
            for row in rows
        }

    def get_event_max_acceptance(
        self,
        db: Session,
        event_id: int | None,
    ) -> int:
        """Return the highest acceptance_count for any session in the event."""
        result = (
            db.query(func.max(SessionPopularity.acceptance_count))
            .filter(SessionPopularity.event_id == event_id)
            .scalar()
        )
        return result or 0

    @staticmethod
    def compute_popularity_score(
        acceptance_count: int,
        max_acceptance: int,
        rejection_count: int = 0,
        exploration_weight: float = 0.0,
        exploration_decay_threshold: int = 20,
    ) -> float:
        """Blend momentum, acceptance quality, and an optional cold-start exploration bonus.

        Components:
        - momentum: log-normalized acceptance_count against event max
        - quality: Bayesian acceptance ratio using a neutral Beta(1, 1) prior
        - exploration bonus (optional): when exploration_weight > 0, sessions with
          zero interactions return 1.0 (cold-start — all unseen sessions rank at the
          top equally until they accumulate data). For sessions with interactions the
          bonus decays toward 0, reaching ~0.01 at exploration_decay_threshold total
          interactions. Bonuses below 0.01 are skipped entirely.
        """
        safe_acceptance = max(0, acceptance_count)
        safe_rejection = max(0, rejection_count)
        total_interactions = safe_acceptance + safe_rejection

        # No event-level peak established yet: neutral.
        if max_acceptance <= 0:
            return 0.5

        # Shortcut: event leader with a clean record.
        if safe_acceptance >= max_acceptance and safe_rejection == 0:
            return 1.0

        momentum = math.log1p(safe_acceptance) / math.log1p(max_acceptance)

        # Bayesian acceptance ratio: Beta(1,1) prior keeps this neutral at 0.5 when
        # a session has exactly 1 accept and 1 reject (equal signal), and approaches
        # the true rate as interactions accumulate.
        quality = (safe_acceptance + 1.0) / (total_interactions + 2.0)

        score = (0.40 * momentum) + (0.60 * quality)

        if exploration_weight > 0:
            # decay_k is chosen so the bonus hits exactly ~0.01 at the threshold.
            decay_k = exploration_decay_threshold / math.log(100)
            bonus = exploration_weight * math.exp(-total_interactions / decay_k)
            if bonus >= 0.01:
                score = score + bonus

        return max(0.0, min(1.0, score))


session_popularity_crud = CRUDSessionPopularity()
