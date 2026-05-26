"""API routes for authenticated users to manage their own API keys."""

import secrets

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import APIKey, User
from app.schemas.api_key import (
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    APIKeyListItem,
    APIKeyListResponse,
)
from app.security.auth import get_auth_context, get_current_user, hash_api_key

logger = structlog.get_logger()

router = APIRouter(prefix="/me/api-keys", tags=["api-keys"])


def _normalize_roles(raw: list[str] | None) -> list[str]:
    """Normalize role lists to deduplicated, sorted values."""
    if not isinstance(raw, list):
        return []

    return sorted({str(role).strip() for role in raw if str(role).strip()})


def _require_interactive_user(current_user: User) -> None:
    """Restrict key management to interactive JWT-authenticated users."""
    auth_method = str(get_auth_context().get("method") or "")
    if auth_method != "jwt" or current_user.type != "human":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key management requires interactive user authentication",
        )


@router.get("", response_model=APIKeyListResponse)
async def list_my_api_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List API keys belonging to the currently authenticated user."""
    _require_interactive_user(current_user)

    keys = (
        db.query(APIKey)
        .filter(APIKey.user_id == current_user.id)
        .order_by(APIKey.created_at.desc())
        .all()
    )

    return APIKeyListResponse(
        keys=[
            APIKeyListItem(
                id=key.id,
                name=key.name,
                allowed_roles=_normalize_roles(key.allowed_roles),
                last_used_at=key.last_used_at,
                created_at=key.created_at,
                deleted_at=key.deleted_at,
            )
            for key in keys
        ]
    )


@router.post("", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_my_api_key(
    payload: APIKeyCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a delegated API key for the currently authenticated user."""
    _require_interactive_user(current_user)

    owner_roles = _normalize_roles(current_user.roles)
    requested_roles = _normalize_roles(payload.allowed_roles)

    if requested_roles and not set(requested_roles).issubset(set(owner_roles)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="allowed_roles must be a subset of the user's roles",
        )

    plain_key = secrets.token_urlsafe(32)
    db_key = APIKey(
        user_id=current_user.id,
        key_hash=hash_api_key(plain_key),
        name=payload.name,
        allowed_roles=requested_roles or None,
    )
    db.add(db_key)
    db.commit()
    db.refresh(db_key)

    logger.info("api_key_created", user_id=current_user.id, api_key_id=db_key.id)

    return APIKeyCreateResponse(
        id=db_key.id,
        name=db_key.name,
        allowed_roles=requested_roles,
        key=plain_key,
        created_at=db_key.created_at,
    )


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_api_key(
    api_key_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete one of the current user's API keys."""
    _require_interactive_user(current_user)

    db_key = (
        db.query(APIKey)
        .filter(
            APIKey.id == api_key_id,
            APIKey.user_id == current_user.id,
            APIKey.deleted_at.is_(None),
        )
        .first()
    )

    if not db_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    from datetime import datetime

    db_key.deleted_at = datetime.utcnow()
    db.commit()

    logger.info("api_key_revoked", user_id=current_user.id, api_key_id=api_key_id)

    return None
