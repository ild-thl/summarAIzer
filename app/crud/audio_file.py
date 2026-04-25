"""CRUD operations for SessionAudioFile."""

from sqlalchemy.orm import Session as SQLSession

from app.database.models import AudioFileProcessingStatus, SessionAudioFile


def create_audio_file(
    db: SQLSession,
    session_id: int,
    original_filename: str,
    s3_raw_key: str,
    file_order: int,
    total_size_bytes: int,
    created_by_user_id: int | None = None,
) -> SessionAudioFile:
    """Create a new SessionAudioFile record (status=pending)."""
    record = SessionAudioFile(
        session_id=session_id,
        original_filename=original_filename,
        s3_raw_key=s3_raw_key,
        file_order=file_order,
        total_size_bytes=total_size_bytes,
        processing_status=AudioFileProcessingStatus.PENDING,
        created_by_user_id=created_by_user_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_audio_file(db: SQLSession, audio_file_id: int) -> SessionAudioFile | None:
    """Get a single SessionAudioFile by ID."""
    return db.query(SessionAudioFile).filter(SessionAudioFile.id == audio_file_id).first()


def get_audio_files_for_session(db: SQLSession, session_id: int) -> list[SessionAudioFile]:
    """List all audio files for a session ordered by file_order."""
    return (
        db.query(SessionAudioFile)
        .filter(SessionAudioFile.session_id == session_id)
        .order_by(SessionAudioFile.file_order)
        .all()
    )


def count_audio_files_for_session(db: SQLSession, session_id: int) -> int:
    """Count audio files for a session (used to compute default file_order)."""
    return db.query(SessionAudioFile).filter(SessionAudioFile.session_id == session_id).count()


def update_audio_file_processed(
    db: SQLSession,
    audio_file_id: int,
    s3_prefix: str,
    chunk_count: int,
) -> SessionAudioFile | None:
    """Mark an audio file as processed, set s3_prefix/chunk_count, clear s3_raw_key."""
    record = get_audio_file(db, audio_file_id)
    if record:
        record.s3_prefix = s3_prefix
        record.chunk_count = chunk_count
        record.s3_raw_key = None
        record.processing_status = AudioFileProcessingStatus.PROCESSED
        record.processing_error = None
        db.commit()
        db.refresh(record)
    return record


def update_audio_file_status(
    db: SQLSession,
    audio_file_id: int,
    status: AudioFileProcessingStatus,
    processing_error: str | None = None,
) -> SessionAudioFile | None:
    """Update processing status (and optional error message)."""
    record = get_audio_file(db, audio_file_id)
    if record:
        record.processing_status = status
        record.processing_error = processing_error
        db.commit()
        db.refresh(record)
    return record


def delete_audio_file(db: SQLSession, audio_file_id: int) -> bool:
    """Delete a SessionAudioFile record. Returns True if deleted."""
    record = get_audio_file(db, audio_file_id)
    if record:
        db.delete(record)
        db.commit()
        return True
    return False
