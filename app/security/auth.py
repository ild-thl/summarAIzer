"""Authentication and authorization utilities."""

import contextvars
import hashlib
import time
from datetime import datetime
from typing import Any

import requests
import structlog
from fastapi import Depends, Header, HTTPException, status
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.connection import get_db
from app.database.models import APIKey, Event, User
from app.database.models import Session as SessionModel

logger = structlog.get_logger()

_AUTH_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "auth_context",
    default={"method": "anonymous", "roles": [], "groups": [], "claims": {}},
)
_JWKS_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}


def hash_api_key(key: str) -> str:
    """Hash API key for secure storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def _set_auth_context(
    method: str,
    claims: dict[str, Any] | None = None,
    roles: list[str] | None = None,
    groups: list[str] | None = None,
) -> None:
    """Store resolved auth metadata for this request context."""
    _AUTH_CONTEXT.set(
        {
            "method": method,
            "claims": claims or {},
            "roles": roles or [],
            "groups": groups or [],
        }
    )


def get_auth_context() -> dict[str, Any]:
    """Get auth context metadata for the current request."""
    return _AUTH_CONTEXT.get()


def _extract_bearer_token(authorization: str | None) -> str:
    """Validate and extract bearer token payload from Authorization header."""
    if not authorization:
        logger.warning("auth_missing_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("auth_invalid_format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format",
        )

    return parts[1]


def _extract_jwt_roles(claims: dict[str, Any], client_id: str) -> list[str]:
    """Extract role names from common Keycloak claim locations."""
    roles: set[str] = set()

    direct_roles = claims.get("roles")
    if isinstance(direct_roles, list):
        roles.update(str(role).strip() for role in direct_roles if str(role).strip())

    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        realm_roles = realm_access.get("roles")
        if isinstance(realm_roles, list):
            roles.update(str(role).strip() for role in realm_roles if str(role).strip())

    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        clients_to_check = [client_id] if client_id else list(resource_access.keys())
        for cid in clients_to_check:
            client_data = resource_access.get(cid)
            if not isinstance(client_data, dict):
                continue
            client_roles = client_data.get("roles")
            if isinstance(client_roles, list):
                roles.update(str(role).strip() for role in client_roles if str(role).strip())

    return sorted(roles)


def _extract_jwt_groups(claims: dict[str, Any]) -> list[str]:
    """Extract group names from JWT claims."""
    raw_groups = claims.get("groups")
    if not isinstance(raw_groups, list):
        return []

    return sorted(str(group).strip() for group in raw_groups if str(group).strip())


def _normalize_role_list(raw: Any) -> list[str]:
    """Normalize raw role-like input to a sorted, deduplicated list of strings."""
    if not isinstance(raw, list):
        return []

    normalized = {str(role).strip() for role in raw if str(role).strip()}
    return sorted(normalized)


def _fetch_jwks(jwks_url: str, ttl_seconds: int) -> dict[str, Any]:
    """Fetch and cache JWKS payload used for JWT signature verification."""
    now = time.time()
    cached_payload = _JWKS_CACHE.get("payload")
    cached_expires = float(_JWKS_CACHE.get("expires_at") or 0)
    if isinstance(cached_payload, dict) and cached_expires > now:
        return cached_payload

    try:
        response = requests.get(jwks_url, timeout=3)
        response.raise_for_status()
        jwks = response.json()
    except requests.RequestException as exc:
        logger.warning("auth_jwks_fetch_failed", error=str(exc), jwks_url=jwks_url)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to validate token",
        ) from exc

    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        logger.warning("auth_jwks_invalid_payload", jwks_url=jwks_url)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to validate token",
        )

    _JWKS_CACHE["payload"] = jwks
    _JWKS_CACHE["expires_at"] = now + max(ttl_seconds, 30)
    return jwks


def _select_jwk_for_token(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
    """Select signing key from JWKS based on token header."""
    keys = jwks.get("keys")
    if not isinstance(keys, list) or len(keys) == 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to validate token",
        )

    header = jwt.get_unverified_header(token)
    token_kid = header.get("kid")

    if token_kid:
        for key in keys:
            if isinstance(key, dict) and key.get("kid") == token_kid:
                return key
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token key identifier",
        )

    if len(keys) == 1 and isinstance(keys[0], dict):
        return keys[0]

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unable to resolve signing key",
    )


def _verify_jwt_token(token: str) -> tuple[dict[str, Any], list[str], list[str]]:
    """Validate JWT and return token claims, roles, and groups."""
    settings = get_settings()

    decode_options = {
        "verify_signature": settings.jwt_verify_signature,
        "verify_exp": settings.jwt_verify_exp,
        "verify_aud": bool(settings.jwt_audience),
        "verify_iss": bool(settings.jwt_issuer),
    }

    key: dict[str, Any] | str | None = None
    if settings.jwt_verify_signature:
        if not settings.jwt_jwks_url:
            logger.warning("auth_jwks_url_missing")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to validate token",
            )
        jwks = _fetch_jwks(settings.jwt_jwks_url, settings.jwt_jwks_cache_ttl_seconds)
        key = _select_jwk_for_token(token, jwks)

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=settings.jwt_algorithms_list,
            audience=settings.jwt_audience or None,
            issuer=settings.jwt_issuer or None,
            options=decode_options,
        )
    except ExpiredSignatureError as exc:
        logger.warning("auth_jwt_expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from exc
    except JWTError as exc:
        logger.warning("auth_jwt_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    if not isinstance(claims, dict) or not str(claims.get("sub") or "").strip():
        logger.warning("auth_jwt_missing_sub")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    roles = _extract_jwt_roles(claims, settings.jwt_client_id)
    groups = _extract_jwt_groups(claims)

    if settings.jwt_admin_group and settings.jwt_admin_group in groups:
        roles = sorted({*roles, settings.jwt_admin_role})

    return claims, roles, groups


def _upsert_human_user_from_claims(
    db: Session,
    claims: dict[str, Any],
    roles: list[str],
    groups: list[str],
) -> User:
    """Find or create a human user based on JWT claims."""
    subject = str(claims.get("sub") or "").strip()
    preferred_username = str(claims.get("preferred_username") or "").strip()
    email = str(claims.get("email") or "").strip() or None
    username = preferred_username or email or subject

    user = db.query(User).filter(User.username == username).first()

    if not user:
        user = db.query(User).filter(User.keycloak_sub == subject).first()

    if not user and email:
        user = db.query(User).filter(User.email == email).first()

    if not user:
        user = User(
            keycloak_sub=subject,
            username=username,
            email=email,
            roles=roles,
            groups=groups,
            type="human",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("auth_user_created_from_jwt", user_id=user.id, username=user.username)
        return user

    has_changes = False
    if user.keycloak_sub != subject:
        user.keycloak_sub = subject
        has_changes = True
    if email and user.email != email:
        user.email = email
        has_changes = True
    if user.roles != roles:
        user.roles = roles
        has_changes = True
    if user.groups != groups:
        user.groups = groups
        has_changes = True
    if user.type != "human":
        user.type = "human"
        has_changes = True

    if has_changes:
        db.commit()
        db.refresh(user)

    return user


def _authenticate_with_api_key(api_key: str, db: Session) -> User:
    """Authenticate using API key."""
    key_hash = hash_api_key(api_key)

    api_key_record = (
        db.query(APIKey)
        .filter(
            APIKey.key_hash == key_hash,
            APIKey.deleted_at.is_(None),
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

    api_key_record.last_used_at = datetime.utcnow()
    db.commit()

    owner_roles = _normalize_role_list(user.roles)
    delegated_role_subset = _normalize_role_list(api_key_record.allowed_roles)
    effective_roles = owner_roles
    if len(delegated_role_subset) > 0:
        effective_roles = sorted(set(owner_roles) & set(delegated_role_subset))

    _set_auth_context(
        method="api_key", roles=effective_roles, groups=_normalize_role_list(user.groups)
    )

    return user


def _authenticate_with_jwt(token: str, db: Session) -> User:
    """Authenticate using Keycloak/OIDC JWT."""
    claims, roles, groups = _verify_jwt_token(token)
    user = _upsert_human_user_from_claims(db, claims, roles=roles, groups=groups)

    if not user.is_active:
        logger.warning("auth_inactive_user", user_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive",
        )

    _set_auth_context(method="jwt", claims=claims, roles=roles, groups=groups)

    return user


def _authenticate_token(token: str, db: Session) -> User:
    """Authenticate bearer token via JWT or API key (always supported)."""
    looks_like_jwt = token.count(".") == 2

    if looks_like_jwt:
        jwt_error: HTTPException | None = None
        try:
            return _authenticate_with_jwt(token, db)
        except HTTPException as exc:
            # Fall back to API key auth so both methods are always available.
            # This also allows API keys containing dots.
            jwt_error = exc

        try:
            return _authenticate_with_api_key(token, db)
        except HTTPException:
            if jwt_error is not None:
                # If token looked like JWT and JWT auth failed, surface that error
                # instead of masking it as "Invalid API key".
                raise jwt_error from None
            raise

    return _authenticate_with_api_key(token, db)


def get_current_user_roles(_current_user: User | None = None) -> set[str]:
    """Return role set resolved from auth context."""
    context = get_auth_context()
    roles = context.get("roles", [])
    if not isinstance(roles, list):
        return set()
    return {str(role).strip() for role in roles if str(role).strip()}


def is_admin(current_user: User | None) -> bool:
    """Check whether the current user has admin privileges."""
    if current_user is None:
        return False

    settings = get_settings()
    return settings.jwt_admin_role in get_current_user_roles(current_user)


def can_manage_event(event: Event, current_user: User) -> bool:
    """Check if user can manage event (admin or event owner)."""
    return is_admin(current_user) or event.owner_id == current_user.id


def can_manage_session(session: SessionModel, current_user: User, db: Session) -> bool:
    """Check if user can manage session (admin, session owner, or event owner)."""
    if is_admin(current_user):
        return True

    if session.owner_id == current_user.id:
        return True

    if session.event_id is None:
        return False

    event_owner_id = None
    if session.event is not None:
        event_owner_id = session.event.owner_id

    if event_owner_id is None:
        event = db.query(Event).filter(Event.id == session.event_id).first()
        event_owner_id = event.owner_id if event else None

    return event_owner_id == current_user.id


async def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate current user from bearer token (JWT or API key)."""
    token = _extract_bearer_token(authorization)
    return _authenticate_token(token, db)


async def require_event_owner(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Validate event write access (admin override + owner check)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if not can_manage_event(event, current_user):
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
    """Validate session write access (admin, session owner, or event owner)."""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if not can_manage_session(session, current_user, db):
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


async def get_current_user_optional(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User | None:
    """Optional authentication. Returns User if valid auth provided, None otherwise."""
    if not authorization:
        _set_auth_context(method="anonymous")
        return None

    try:
        token = _extract_bearer_token(authorization)
        return _authenticate_token(token, db)
    except Exception:
        _set_auth_context(method="anonymous")
        return None


def can_access_session_content(
    session: SessionModel,
    current_user: User | None,
) -> bool:
    """
    Check if a user can access a session/content based on publication status.

    - Published sessions: accessible to everyone
    - Draft sessions: accessible to admin, owner, and event owner
    """
    from app.database.models import SessionStatus

    if session.status == SessionStatus.PUBLISHED.value:
        return True

    if not current_user:
        return False

    if is_admin(current_user):
        return True

    if session.owner_id == current_user.id:
        return True

    if session.event is not None and session.event.owner_id == current_user.id:
        return True

    return False
