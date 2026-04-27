"""API routes for Session Content Management (sub-resource)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)

from app.async_jobs.tasks import process_audio_upload
from app.crud import audio_file as audio_file_crud
from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import AudioFileProcessingStatus, User
from app.database.models import Session as SessionModel
from app.schemas.content import (
    AudioFileListResponse,
    AudioFileResponse,
    GeneratedContentCreate,
    GeneratedContentResponse,
    GeneratedContentUpdate,
    SessionContentListResponse,
)
from app.security.auth import (
    can_access_session_content,
    get_current_user_optional,
    require_session_owner,
)
from app.services.s3_audio_service import get_s3_audio_service

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["session-content"])


@router.get("/{session_id}/content", response_model=SessionContentListResponse)
async def get_available_content(
    session_id: int,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get list of available content identifiers for session (public access to published sessions only)."""
    db_session = session_crud.read(db, session_id)
    if not db_session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Check access based on publication status
    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "content_list_access_denied",
            session_id=session_id,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return SessionContentListResponse(available_content=db_session.available_content_identifiers)


@router.get(
    "/{session_id}/content/{identifier}",
    response_model=GeneratedContentResponse,
)
async def get_content_by_identifier(
    session_id: int,
    identifier: str,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    Retrieve latest generated content for a session and identifier.

    Public access to published sessions only; drafts visible only to owner.
    """
    db_session = session_crud.read(db, session_id)
    if not db_session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Check access based on publication status
    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "content_access_denied",
            session_id=session_id,
            identifier=identifier,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    db_content = content_crud.get_content_by_identifier(db, session_id, identifier)
    if not db_content:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Content with identifier '{identifier}' not found for this session",
        )

    return db_content


@router.post(
    "/{session_id}/content/{identifier}",
    response_model=GeneratedContentResponse,
    status_code=HTTP_201_CREATED,
)
async def create_content(
    session_id: int,
    identifier: str,
    content_in: GeneratedContentCreate,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Create content with a specific identifier (owner only).

    This is a generic endpoint that allows creating any type of content by specifying the identifier.
    For example:
    - POST /sessions/1/content/transcription
    - POST /sessions/1/content/notes
    - POST /sessions/1/content/custom-data
    """
    # Check if content with this identifier already exists
    existing = content_crud.get_content_by_identifier(db, session_id, identifier)
    if existing:
        logger.warning(
            "content_already_exists",
            session_id=session_id,
            identifier=identifier,
        )
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"Content with identifier '{identifier}' already exists. Delete and re-create to update.",
        )

    # Create the content
    db_content = content_crud.create_content(
        db=db,
        session_id=session_id,
        identifier=identifier,
        content=content_in.content,
        content_type=content_in.content_type or "plain_text",
        workflow_execution_id=None,  # Manually provided
        meta_info=content_in.meta_info,
    )

    # Update session's available content
    session_crud.add_available_content_identifier(db, session_id, identifier)

    logger.info(
        "content_created",
        session_id=session_id,
        identifier=identifier,
        content_id=db_content.id,
    )

    return db_content


@router.patch(
    "/{session_id}/content/{identifier}",
    response_model=GeneratedContentResponse,
)
async def update_content(
    session_id: int,
    identifier: str,
    content_in: GeneratedContentUpdate,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Update generated content (owner only, e.g., manual edits after generation)."""
    db_content = content_crud.get_content_by_identifier(db, session_id, identifier)
    if not db_content:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Content with identifier '{identifier}' not found",
        )

    updated = content_crud.update_content(
        db=db,
        content_id=db_content.id,
        content=content_in.content,
        meta_info=content_in.meta_info,
    )

    logger.info(
        "content_updated",
        session_id=session_id,
        identifier=identifier,
        content_id=db_content.id,
    )

    return updated


@router.delete(
    "/{session_id}/content/{identifier}",
    status_code=HTTP_204_NO_CONTENT,
)
async def delete_content(
    session_id: int,
    identifier: str,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Delete content with specific identifier (owner only)."""
    if not content_crud.delete_content_by_identifier(db, session_id, identifier):
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Content with identifier '{identifier}' not found",
        )

    # Remove from session's available content
    session_crud.remove_available_content_identifier(db, session_id, identifier)

    logger.info(
        "content_deleted",
        session_id=session_id,
        identifier=identifier,
    )

    return None


# ---------------------------------------------------------------------------
# Audio file endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/audio-files",
    response_model=AudioFileResponse,
    status_code=HTTP_201_CREATED,
)
async def upload_audio_file(
    session_id: int,
    file: UploadFile,
    file_order: int | None = None,
    _: SessionModel = Depends(require_session_owner),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    Upload a raw audio file for a session (owner only).

    The file is stored in S3 immediately. A Celery task is queued to convert
    it to FLAC chunks. The returned record will have status=pending until
    the task completes.
    """
    if not file.filename:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="filename is required")

    raw_data = await file.read()
    if not raw_data:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    # Determine file_order: default to count+1 per file uploaded (1-based)
    if file_order is None:
        file_order = audio_file_crud.count_audio_files_for_session(db, session_id) + 1

    # Create DB record first so we have an ID for S3 key construction
    record = audio_file_crud.create_audio_file(
        db=db,
        session_id=session_id,
        original_filename=file.filename,
        s3_raw_key="__placeholder__",  # replaced below
        file_order=file_order,
        total_size_bytes=len(raw_data),
        created_by_user_id=current_user.id if current_user else None,
    )

    # Upload raw file to S3
    s3 = get_s3_audio_service()
    raw_key = s3.upload_raw(session_id, record.id, file.filename, raw_data)

    # Update record with real S3 key
    record.s3_raw_key = raw_key
    db.commit()
    db.refresh(record)

    # Queue Celery processing task
    process_audio_upload.delay(record.id)

    logger.info(
        "audio_file_uploaded",
        session_id=session_id,
        audio_file_id=record.id,
        original_filename=file.filename,
        size_bytes=len(raw_data),
        file_order=file_order,
    )

    return record


@router.get(
    "/{session_id}/audio-files",
    response_model=AudioFileListResponse,
)
async def list_audio_files(
    session_id: int,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """List all audio files for a session (owner only)."""
    files = audio_file_crud.get_audio_files_for_session(db, session_id)
    return AudioFileListResponse(audio_files=files)


@router.delete(
    "/{session_id}/audio-files/{audio_file_id}",
    status_code=HTTP_204_NO_CONTENT,
)
async def delete_audio_file(
    session_id: int,
    audio_file_id: int,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Delete an audio file record and its S3 objects (owner only).

    Deletion of PENDING/PROCESSING files is blocked to avoid race conditions
    with in-flight Celery tasks.
    """
    record = audio_file_crud.get_audio_file(db, audio_file_id)
    if not record or record.session_id != session_id:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Audio file not found")

    if record.processing_status in (
        AudioFileProcessingStatus.PENDING,
        AudioFileProcessingStatus.PROCESSING,
    ):
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete audio file with status '{record.processing_status.value}'. "
                "Wait for processing to complete or fail first."
            ),
        )

    # Clean up S3 objects
    s3 = get_s3_audio_service()
    if record.s3_raw_key:
        try:
            s3.delete_object(record.s3_raw_key)
        except Exception:
            logger.warning(
                "audio_file_delete_s3_raw_failed",
                audio_file_id=audio_file_id,
                s3_raw_key=record.s3_raw_key,
            )
    if record.s3_prefix:
        try:
            s3.delete_prefix(record.s3_prefix)
        except Exception:
            logger.warning(
                "audio_file_delete_s3_chunks_failed",
                audio_file_id=audio_file_id,
                s3_prefix=record.s3_prefix,
            )

    audio_file_crud.delete_audio_file(db, audio_file_id)

    logger.info(
        "audio_file_deleted",
        session_id=session_id,
        audio_file_id=audio_file_id,
    )

    return None
