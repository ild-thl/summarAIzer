"""Unit tests for session popularity CRUD and scoring."""

from unittest.mock import MagicMock

import pytest

from app.crud.session_popularity import CRUDSessionPopularity, session_popularity_crud
from app.database.models import SessionPopularity

# ---------------------------------------------------------------------------
# compute_popularity_score (pure function - no DB needed)
# ---------------------------------------------------------------------------


class TestComputePopularityScore:
    def test_returns_neutral_when_no_event_data(self):
        score = CRUDSessionPopularity.compute_popularity_score(0, 0)
        assert score == 0.5

    def test_returns_neutral_for_max_zero(self):
        score = CRUDSessionPopularity.compute_popularity_score(10, 0)
        assert score == 0.5

    def test_top_session_returns_one(self):
        score = CRUDSessionPopularity.compute_popularity_score(100, 100, 0)
        assert score == pytest.approx(1.0)

    def test_zero_count_unseen_returns_neutral(self):
        # Without exploration, an unseen session should be exactly neutral.
        score = CRUDSessionPopularity.compute_popularity_score(0, 100)
        assert score == 0.5

    def test_exploration_unseen_returns_one(self):
        # With exploration active, unseen sessions cold-start at 1.0.
        score = CRUDSessionPopularity.compute_popularity_score(0, 100, exploration_weight=0.3)
        assert score == 1.0

    def test_exploration_decays_with_interactions(self):
        # Score should decrease as a session accumulates interactions.
        s1 = CRUDSessionPopularity.compute_popularity_score(1, 100, 0, exploration_weight=0.5)
        s5 = CRUDSessionPopularity.compute_popularity_score(5, 100, 0, exploration_weight=0.5)
        s10 = CRUDSessionPopularity.compute_popularity_score(10, 100, 0, exploration_weight=0.5)
        assert s1 > s5 > s10

    def test_exploration_bonus_negligible_at_threshold(self):
        # At the default decay threshold (20), the bonus should be < 0.01
        # and therefore not added — score equals the base quality score.
        score_with = CRUDSessionPopularity.compute_popularity_score(
            20, 100, 0, exploration_weight=0.5, exploration_decay_threshold=20
        )
        score_without = CRUDSessionPopularity.compute_popularity_score(20, 100, 0)
        assert score_with == pytest.approx(score_without, abs=0.011)

    def test_exploration_bounded_between_zero_and_one(self):
        for n in [0, 1, 5, 10, 20, 30]:
            score = CRUDSessionPopularity.compute_popularity_score(
                n, 100, 0, exploration_weight=0.5
            )
            assert 0.0 <= score <= 1.0

    def test_exploration_threshold_configurable(self):
        # A stricter threshold (10) should make the bonus negligible sooner.
        score_at_10_strict = CRUDSessionPopularity.compute_popularity_score(
            10, 100, 0, exploration_weight=0.5, exploration_decay_threshold=10
        )
        score_at_10_lenient = CRUDSessionPopularity.compute_popularity_score(
            10, 100, 0, exploration_weight=0.5, exploration_decay_threshold=30
        )
        assert score_at_10_lenient > score_at_10_strict

    def test_rejections_reduce_score(self):
        high_quality = CRUDSessionPopularity.compute_popularity_score(50, 100, 0)
        low_quality = CRUDSessionPopularity.compute_popularity_score(50, 100, 50)
        assert high_quality > low_quality

    def test_bounded_between_zero_and_one(self):
        for count in range(0, 110, 10):
            score = CRUDSessionPopularity.compute_popularity_score(count, 100)
            assert 0.0 <= score <= 1.0

    def test_low_exposure_high_quality_beats_heavily_rejected_legacy(self):
        # A low-exposure but clean session should outscore a heavily-rejected
        # high-volume session — quality wins over raw acceptance count.
        overlooked = CRUDSessionPopularity.compute_popularity_score(10, 300, 0)
        legacy_rejected = CRUDSessionPopularity.compute_popularity_score(100, 300, 200)
        assert overlooked > legacy_rejected

    def test_high_volume_high_quality_scores_well(self):
        # The leading session (even with some rejections) should score clearly above
        # an unseen session and well above 0.7.
        top = CRUDSessionPopularity.compute_popularity_score(300, 300, 120)
        assert top > 0.75


# ---------------------------------------------------------------------------
# get_event_max_acceptance
# ---------------------------------------------------------------------------


class TestGetEventMaxAcceptance:
    def _make_db(self, scalar_result):
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = scalar_result
        return db

    def test_returns_max_value(self):
        db = self._make_db(42)
        result = session_popularity_crud.get_event_max_acceptance(db, event_id=1)
        assert result == 42

    def test_returns_zero_when_no_rows(self):
        db = self._make_db(None)
        result = session_popularity_crud.get_event_max_acceptance(db, event_id=1)
        assert result == 0


# ---------------------------------------------------------------------------
# get_popularity_map
# ---------------------------------------------------------------------------


class TestGetPopularityMap:
    def test_empty_session_ids_returns_empty(self):
        db = MagicMock()
        result = session_popularity_crud.get_popularity_map(db, session_ids=[], event_id=1)
        assert result == {}
        db.query.assert_not_called()

    def test_maps_session_ids_to_counts(self):
        row1 = MagicMock(session_id=1, acceptance_count=10, rejection_count=2)
        row2 = MagicMock(session_id=2, acceptance_count=5, rejection_count=0)

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [row1, row2]

        result = session_popularity_crud.get_popularity_map(db, session_ids=[1, 2], event_id=1)

        assert result[1] == {"acceptance_count": 10, "rejection_count": 2}
        assert result[2] == {"acceptance_count": 5, "rejection_count": 0}

    def test_missing_sessions_not_in_result(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = session_popularity_crud.get_popularity_map(db, session_ids=[99], event_id=1)
        assert result == {}


# ---------------------------------------------------------------------------
# record_interactions - DB integration via SQLite
# ---------------------------------------------------------------------------


class TestRecordInteractions:
    @pytest.fixture
    def db(self, test_db):
        return test_db

    def test_creates_new_row_for_acceptance(self, db, sample_event, sample_session):
        session_popularity_crud.record_interactions(
            db=db,
            accepted_ids=[sample_session.id],
            rejected_ids=[],
            event_id=sample_event.id,
        )

        row = (
            db.query(SessionPopularity)
            .filter_by(session_id=sample_session.id, event_id=sample_event.id)
            .first()
        )
        assert row is not None
        assert row.acceptance_count == 1
        assert row.rejection_count == 0

    def test_creates_new_row_for_rejection(self, db, sample_event, sample_session):
        session_popularity_crud.record_interactions(
            db=db,
            accepted_ids=[],
            rejected_ids=[sample_session.id],
            event_id=sample_event.id,
        )

        row = (
            db.query(SessionPopularity)
            .filter_by(session_id=sample_session.id, event_id=sample_event.id)
            .first()
        )
        assert row is not None
        assert row.acceptance_count == 0
        assert row.rejection_count == 1

    def test_increments_existing_row(self, db, sample_event, sample_session):
        for _ in range(3):
            session_popularity_crud.record_interactions(
                db=db,
                accepted_ids=[sample_session.id],
                rejected_ids=[],
                event_id=sample_event.id,
            )

        row = (
            db.query(SessionPopularity)
            .filter_by(session_id=sample_session.id, event_id=sample_event.id)
            .first()
        )
        assert row.acceptance_count == 3

    def test_increments_both_counts_independently(self, db, sample_event, sample_session):
        session_popularity_crud.record_interactions(
            db=db,
            accepted_ids=[sample_session.id],
            rejected_ids=[sample_session.id],
            event_id=sample_event.id,
        )

        row = (
            db.query(SessionPopularity)
            .filter_by(session_id=sample_session.id, event_id=sample_event.id)
            .first()
        )
        assert row.acceptance_count == 1
        assert row.rejection_count == 1

    def test_empty_ids_creates_no_rows(self, db, sample_event):
        session_popularity_crud.record_interactions(
            db=db,
            accepted_ids=[],
            rejected_ids=[],
            event_id=sample_event.id,
        )

        count = db.query(SessionPopularity).count()
        assert count == 0

    def test_scoped_to_event(self, db, sample_event, sample_session):
        """Counts for different event_ids are tracked separately."""
        session_popularity_crud.record_interactions(
            db=db,
            accepted_ids=[sample_session.id],
            rejected_ids=[],
            event_id=sample_event.id,
        )
        session_popularity_crud.record_interactions(
            db=db,
            accepted_ids=[sample_session.id],
            rejected_ids=[],
            event_id=None,  # global scope
        )

        rows = db.query(SessionPopularity).filter_by(session_id=sample_session.id).all()
        assert len(rows) == 2
        event_row = next(r for r in rows if r.event_id == sample_event.id)
        global_row = next(r for r in rows if r.event_id is None)
        assert event_row.acceptance_count == 1
        assert global_row.acceptance_count == 1

    def test_db_error_does_not_propagate(self, sample_event, sample_session):
        """record_interactions should swallow DB errors gracefully."""
        crud = CRUDSessionPopularity()
        broken_db = MagicMock()
        broken_db.query.return_value.filter.return_value.first.return_value = None
        broken_db.commit.side_effect = Exception("DB down")

        # Should not raise
        crud.record_interactions(
            db=broken_db,
            accepted_ids=[sample_session.id],
            rejected_ids=[],
            event_id=sample_event.id,
        )
        broken_db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# list_with_filters - popularity_sort
# ---------------------------------------------------------------------------


class TestListWithFiltersPopularitySort:
    def test_popularity_sort_disables_random(self, test_db, sample_event):
        """When popularity_sort=True, randomize is ignored and ordering is by popularity."""
        from app.crud.session import session_crud

        # Just verify it doesn't raise and returns a list
        result = session_crud.list_with_filters(
            db=test_db,
            event_id=sample_event.id,
            popularity_sort=True,
        )
        assert isinstance(result, list)

    def test_popularity_sort_orders_by_acceptance_desc(self, test_db, sample_event):
        """Sessions with higher acceptance_count should come first."""
        from datetime import datetime, timedelta

        from app.crud.session import session_crud
        from app.database.models import Session as SessionModel
        from app.database.models import (
            SessionFormat,
            SessionPopularity,
            SessionStatus,
        )

        now = datetime.utcnow()
        sessions = []
        for i, title in enumerate(["Low", "High", "Mid"]):
            s = SessionModel(
                title=title,
                description=f"Desc {i}",
                short_description=f"Short {i}",
                start_datetime=now + timedelta(hours=i),
                end_datetime=now + timedelta(hours=i + 1),
                status=SessionStatus.PUBLISHED,
                session_format=SessionFormat.WORKSHOP,
                language="en",
                uri=f"popularity-sort-test-{i}",
                event_id=sample_event.id,
                duration=60,
            )
            test_db.add(s)
        test_db.flush()
        # Reload to get IDs
        sessions = (
            test_db.query(SessionModel).filter(SessionModel.event_id == sample_event.id).all()
        )

        # Assign acceptance counts: Low=1, High=10, Mid=5
        counts = {"Low": 1, "High": 10, "Mid": 5}
        for s in sessions:
            if s.title in counts:
                pop = SessionPopularity(
                    session_id=s.id,
                    event_id=sample_event.id,
                    acceptance_count=counts[s.title],
                    rejection_count=0,
                    updated_at=now,
                )
                test_db.add(pop)
        test_db.commit()

        result = session_crud.list_with_filters(
            db=test_db,
            event_id=sample_event.id,
            popularity_sort=True,
        )

        titles = [s.title for s in result if s.title in counts]
        assert titles.index("High") < titles.index("Mid")
        assert titles.index("Mid") < titles.index("Low")
