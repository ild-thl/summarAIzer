"""CRUD operations for SessionPopularity model."""

import math
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

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

        for session_id, field in (
            *((sid, "acceptance_count") for sid in accepted_ids),
            *((sid, "rejection_count") for sid in rejected_ids),
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

        try:
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
    def compute_popularity_score(acceptance_count: int, max_acceptance: int) -> float:
        """Log-normalize acceptance_count against the event maximum.

        score = log(1 + count) / log(1 + max_count)

        Returns 0.5 (neutral) when no popularity data exists for the event yet.
        Returns a value in [0, 1] otherwise.
        """
        if max_acceptance <= 0:
            return 0.5
        return math.log1p(acceptance_count) / math.log1p(max_acceptance)


session_popularity_crud = CRUDSessionPopularity()
