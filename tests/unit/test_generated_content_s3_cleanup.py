import json
from datetime import datetime, timedelta

from app.crud import generated_content as content_crud
from app.database.models import Event
from app.database.models import Session as SessionModel


def _create_event_and_session(db):
    now = datetime.utcnow()
    event = Event(title="E", start_date=now, end_date=now + timedelta(days=1), uri="e1")
    db.add(event)
    db.commit()
    db.refresh(event)

    session = SessionModel(
        title="S",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        uri="s1",
        event_id=event.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return event, session


def test_delete_content_by_identifier_calls_s3_delete(test_db, monkeypatch):
    _, session = _create_event_and_session(test_db)

    key = "content/summaraizer/session_1/foo.png"
    content_crud.create_content(
        db=test_db,
        session_id=session.id,
        identifier="image",
        content=json.dumps({"s3_key": key, "resource_url": f"https://example.com/{key}"}),
        content_type="image",
    )

    calls = []

    class DummySvc:
        def delete_object(self, k):
            calls.append(k)
            return True

    monkeypatch.setattr(content_crud, "get_s3_service", lambda: DummySvc())

    assert content_crud.delete_content_by_identifier(test_db, session.id, "image")
    assert calls == [key]


def test_create_or_update_replaces_old_s3_and_calls_delete(test_db, monkeypatch):
    _, session = _create_event_and_session(test_db)

    old_key = "content/summaraizer/session_1/old.png"
    new_key = "content/summaraizer/session_1/new.png"

    content_crud.create_content(
        db=test_db,
        session_id=session.id,
        identifier="image",
        content=json.dumps({"s3_key": old_key}),
        content_type="image",
    )

    calls = []

    class DummySvc:
        def delete_object(self, k):
            calls.append(k)
            return True

    monkeypatch.setattr(content_crud, "get_s3_service", lambda: DummySvc())

    updated = content_crud.create_or_update_content(
        db=test_db,
        session_id=session.id,
        identifier="image",
        content=json.dumps({"s3_key": new_key}),
        content_type="image",
    )

    assert updated is not None
    assert calls == [old_key]
