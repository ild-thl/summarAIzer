"""API routes for Session Content Management (sub-resource)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)

from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import Session as SessionModel
from app.database.models import User
from app.schemas.content import (
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
