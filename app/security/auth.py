"""Authentication and authorization utilities."""

import hashlib
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Header, Depends, status
from sqlalchemy.orm import Session
import structlog

from app.database.connection import get_db
from app.database.models import User, APIKey, Event, Session as SessionModel

logger = structlog.get_logger()


def hash_api_key(key: str) -> str:
    """Hash API key for secure storage."""
    return hashlib.sha256(key.encode()).hexdigest()


async def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """Extract and validate API key from Authorization header, return associated User."""
    if not authorization:
        logger.warning("auth_missing_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    # Expected format: "Bearer {api_key}"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("auth_invalid_format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format",
        )

    api_key = parts[1]
    key_hash = hash_api_key(api_key)

    # Query APIKey by (hashed) key hash
    api_key_record = (
        db.query(APIKey)
        .filter(
            APIKey.key_hash == key_hash,
            APIKey.deleted_at == None,  # Not soft-deleted
        )
        .first()
    )

    if not api_key_record:
        logger.warning("auth_invalid_key", key_prefix=api_key[:4])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    user = api_key_record.user
    if not user or not user.is_active:
        logger.warning(
            "auth_inactive_user",
            user_id=user.id if user else None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive",
        )

    # Update last used timestamp for audit trail
    api_key_record.last_used_at = datetime.utcnow()
    db.commit()

    return user


async def require_event_owner(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Validate that current user owns the event."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )
    if event.owner_id != current_user.id:
        logger.warning(
            "auth_unauthorized_event_access",
            user_id=current_user.id,
            event_id=event_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )
    return event


async def require_session_owner(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionModel:
    """Validate that current user owns the session."""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if session.owner_id != current_user.id:
        logger.warning(
            "auth_unauthorized_session_access",
            user_id=current_user.id,
            session_id=session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )
    return session
