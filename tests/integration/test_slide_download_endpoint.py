"""Integration tests for slide deck download endpoint."""

from datetime import datetime, timedelta
from unittest.mock import Mock

from app.crud.generated_content import create_content
from app.database.models import Session as SessionModel
from app.database.models import SessionStatus


def test_download_slide_file_returns_pdf(client, test_db, sample_event, sample_user, monkeypatch):
    """Published session slide deck should be downloadable via API endpoint."""
    now = datetime.utcnow()
    session = SessionModel(
        title="Slides Session",
        uri="slides-session",
        event_id=sample_event.id,
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.PUBLISHED,
        owner_id=sample_user.id,
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)

    create_content(
        db=test_db,
        session_id=session.id,
        identifier="slide_deck",
        content_type="json",
        content='{"s3_key":"content/summaraizer/slides/session_5/deck.pdf","filename":"deck.pdf","size":11}',
        meta_info={"filename": "deck.pdf", "size": 11},
    )

    mock_s3 = Mock()
    mock_s3.download_slide.return_value = b"%PDF-1.7 demo"
    monkeypatch.setattr(
        "app.routes.session_content.get_s3_slide_service",
        lambda: mock_s3,
    )

    response = client.get(f"/api/v2/sessions/{session.id}/slide-files/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "deck.pdf" in response.headers.get("content-disposition", "")
    assert response.content.startswith(b"%PDF")
