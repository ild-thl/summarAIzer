"""Unit tests for session ownership claim CRUD logic."""

from datetime import datetime, timedelta

from app.crud.session import session_crud
from app.database.models import Event, SessionOwnershipClaimStatus, User
from app.database.models import Session as SessionModel


def _create_user(test_db, username: str) -> User:
    user = User(username=username, type="human", is_active=True)
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _create_session(test_db, owner: User) -> SessionModel:
    now = datetime.utcnow()
    event = Event(
        title="Claim Event",
        start_date=now,
        end_date=now + timedelta(days=1),
        uri=f"event-{owner.id}",
        owner_id=owner.id,
    )
    test_db.add(event)
    test_db.commit()
    test_db.refresh(event)

    session = SessionModel(
        title="Claim Session",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        uri=f"session-{owner.id}",
        event_id=event.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    session_crud.add_owner(test_db, session.id, owner.id, added_by_user_id=owner.id)
    return session


def test_create_ownership_claim_is_idempotent_for_pending(test_db):
    """Creating the same pending claim twice returns the existing pending claim."""
    owner = _create_user(test_db, "owner-claim-idempotent")
    requester = _create_user(test_db, "requester-claim-idempotent")
    session = _create_session(test_db, owner)

    claim_1 = session_crud.create_ownership_claim(test_db, session.id, requester.id, "please")
    claim_2 = session_crud.create_ownership_claim(test_db, session.id, requester.id, "again")

    assert claim_1.id == claim_2.id
    assert claim_2.status == SessionOwnershipClaimStatus.PENDING


def test_approve_claim_adds_session_owner(test_db):
    """Approving a pending claim links the requester as a session owner."""
    owner = _create_user(test_db, "owner-approve")
    reviewer = _create_user(test_db, "reviewer-approve")
    requester = _create_user(test_db, "requester-approve")
    session = _create_session(test_db, owner)

    claim = session_crud.create_ownership_claim(test_db, session.id, requester.id, None)
    reviewed = session_crud.review_ownership_claim(
        test_db,
        claim=claim,
        reviewer_user_id=reviewer.id,
        approve=True,
        review_note="ok",
    )

    assert reviewed.status == SessionOwnershipClaimStatus.APPROVED
    assert reviewed.reviewed_by_user_id == reviewer.id
    assert session_crud.is_session_owner(test_db, session.id, requester.id) is True


def test_reject_claim_does_not_add_session_owner(test_db):
    """Rejecting a claim keeps requester without manage privileges."""
    owner = _create_user(test_db, "owner-reject")
    reviewer = _create_user(test_db, "reviewer-reject")
    requester = _create_user(test_db, "requester-reject")
    session = _create_session(test_db, owner)

    claim = session_crud.create_ownership_claim(test_db, session.id, requester.id, None)
    reviewed = session_crud.review_ownership_claim(
        test_db,
        claim=claim,
        reviewer_user_id=reviewer.id,
        approve=False,
        review_note="no",
    )

    assert reviewed.status == SessionOwnershipClaimStatus.REJECTED
    assert session_crud.is_session_owner(test_db, session.id, requester.id) is False
