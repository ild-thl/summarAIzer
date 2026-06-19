"""API routes for Session CRUD management (core resource)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, cast, exists, func, or_
from sqlalchemy.orm import Session, joinedload
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)

from app.crud.event import event_crud
from app.crud.session import session_crud
from app.database.connection import get_db
from app.database.models import (
    Event,
    SessionOwner,
    SessionOwnershipClaim,
    SessionOwnershipClaimStatus,
    SessionStatus,
    User,
)
from app.database.models import Session as SessionModel
from app.schemas.session import (
    PaginationMeta,
    SessionCreate,
    SessionDocumentationResponse,
    SessionListResponse,
    SessionOwnerAddRequest,
    SessionOwnerLinkResponse,
    SessionOwnershipClaimCreate,
    SessionOwnershipClaimResponse,
    SessionOwnershipClaimReview,
    SessionOwnershipClaimSummaryResponse,
    SessionPageResponse,
    SessionResponse,
    SessionUpdate,
)
from app.security.auth import (
    can_access_session_content,
    can_manage_session,
    get_current_user,
    get_current_user_optional,
    is_admin,
    require_session_owner,
)
from app.services.documentation_builder import DocumentationBuilder
from app.crud import generated_content as content_crud
from app.utils.helpers import DateTimeUtils
from app.utils.matomo import track_list_sessions_usage

logger = structlog.get_logger()

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _extract_cover_image_url(session: SessionModel) -> str | None:
    """Extract a cover image URL from the published documentation artifact if available."""
    artifact = session.published_documentation_artifact
    if not isinstance(artifact, dict):
        return None

    sections = artifact.get("sections")
    if not isinstance(sections, list):
        return None

    preferred_identifiers = {"cover_image", "cover", "hero_image"}

    def _is_image_url(value: object) -> bool:
        if not isinstance(value, str) or value.strip() == "":
            return False
        lowered = value.lower()
        return any(
            ext in lowered for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg"]
        )

    first_any: object | None = None
    for section in sections:
        if not isinstance(section, dict):
            continue

        identifier = str(section.get("identifier") or "").strip().lower()
        url = section.get("resource_url")
        if _is_image_url(url):
            if identifier in preferred_identifiers:
                return str(url)
            if first_any is None:
                first_any = url

    return str(first_any) if first_any is not None else None


def _resolve_session_sort(sort_by: str, sort_dir: str):
    """Resolve supported session sort options with stable secondary ordering."""
    sort_fields = {
        "id": SessionModel.id,
        "title": SessionModel.title,
        "start_datetime": SessionModel.start_datetime,
        "created_at": SessionModel.created_at,
        "updated_at": SessionModel.updated_at,
    }
    column = sort_fields.get(sort_by)
    if column is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid sort_by. Allowed: id,title,start_datetime,created_at,updated_at",
        )

    if sort_dir == "asc":
        return [column.asc(), SessionModel.id.asc()]

    return [column.desc(), SessionModel.id.desc()]


def _resolve_public_session_sort(sort_by: str, sort_dir: str):
    """Resolve public session page sort options with stable secondary ordering."""
    normalized = (sort_by or "start_date").strip().lower()
    sort_fields = {
        "start_date": SessionModel.start_datetime,
        "updated_last": SessionModel.updated_at,
        "alphabetical": SessionModel.title,
    }
    column = sort_fields.get(normalized)
    if column is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid sort_by. Allowed: start_date,updated_last,alphabetical",
        )

    normalized_dir = (sort_dir or "asc").strip().lower()
    if normalized_dir not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="Invalid sort_dir. Allowed: asc,desc")

    if normalized == "updated_last" and sort_dir is None:
        normalized_dir = "desc"

    if normalized_dir == "asc":
        return [column.asc(), SessionModel.id.asc()]

    return [column.desc(), SessionModel.id.desc()]


def _split_csv(value: str | None, *, lowercase: bool = False) -> list[str] | None:
    """Split a comma-separated string into a list of trimmed values or return None."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return [i.lower() for i in items] if lowercase else items


@router.get("/page", response_model=SessionPageResponse)
async def list_sessions_page(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=200, description="Maximum records to return"),
    status: str = Query(
        None, description="Filter by status - comma-separated (draft, published) - OR logic"
    ),
    event_id: int = Query(None, description="Filter by event ID"),
    session_format: str = Query(
        None,
        description="Filter by session format - comma-separated (input, lighting talk, diskussion, workshop, training, lab, other) - OR logic",
    ),
    tags: str = Query(None, description="Filter by tags (comma-separated, OR logic)"),
    location_cities: str | None = Query(
        None, description="Filter by city (comma-separated, OR logic)"
    ),
    location_names: str | None = Query(
        None,
        description="Filter by location name such as stage or room (comma-separated, OR logic)",
    ),
    language: str = Query(
        None,
        description="Filter by language - comma-separated (ISO 639-1 code, e.g., en,de) - OR logic",
    ),
    duration_min: int = Query(None, ge=0, description="Minimum duration in minutes"),
    duration_max: int = Query(None, ge=0, description="Maximum duration in minutes"),
    speaker: str = Query(None, description="Search for speaker name"),
    time_windows: str | None = Query(
        None,
        description='JSON array of time windows, e.g. [{"start":"2024-06-01T10:00:00","end":"2024-06-01T11:30:00"}]',
    ),
    search: str = Query(None, description="Full-text search on title, description, and speakers"),
    sort_by: str = Query(
        "start_date",
        description="Sort mode: start_date, updated_last, alphabetical",
    ),
    sort_dir: str = Query("asc", description="Sort direction: asc or desc"),
    exclude_without_artifact: bool = Query(
        False,
        description="Exclude sessions without a published documentation artifact",
    ),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Paginated public session listing with pagination metadata."""
    from app.database.models import SessionFormat

    status_list = _validate_and_parse_enum_list(status, SessionStatus, "status") if status else None
    session_format_list = (
        _validate_and_parse_enum_list(session_format, SessionFormat, "session_format")
        if session_format
        else None
    )

    language_list = _split_csv(language, lowercase=True)

    location_cities_list = _split_csv(location_cities)

    location_names_list = _split_csv(location_names)

    parsed_time_windows = DateTimeUtils.parse_time_windows_json(time_windows)

    tags_list = _split_csv(tags)

    query = db.query(SessionModel).options(joinedload(SessionModel.location_rel))

    if location_cities_list or location_names_list:
        from app.database.models import SessionLocation

        query = query.outerjoin(SessionLocation, SessionLocation.session_id == SessionModel.id)

    if not current_user:
        query = query.filter(SessionModel.status == SessionStatus.PUBLISHED.value)
    elif not is_admin(current_user):
        query = query.outerjoin(Event, SessionModel.event_id == Event.id).filter(
            or_(
                SessionModel.status == SessionStatus.PUBLISHED.value,
                SessionModel.owners.any(User.id == current_user.id),
                and_(SessionModel.event_id.is_not(None), Event.owner_id == current_user.id),
            )
        )

    filters = session_crud._build_session_filters(
        status=status_list,
        event_id=event_id,
        session_format=session_format_list,
        tags=tags_list,
        location_cities=location_cities_list,
        location_names=location_names_list,
        language=language_list,
        duration_min=duration_min,
        duration_max=duration_max,
        speaker=speaker,
        time_windows=parsed_time_windows,
        search=search,
    )

    for filter_condition in filters:
        query = query.filter(filter_condition)

    if exclude_without_artifact:
        # JSON columns may contain JSON null even when SQL value is not NULL.
        # Exclude both SQL NULL and JSON null to keep only real artifact payloads.
        query = query.filter(SessionModel.published_documentation_artifact.is_not(None))
        query = query.filter(cast(SessionModel.published_documentation_artifact, String) != "null")

    total = query.with_entities(func.count(func.distinct(SessionModel.id))).scalar() or 0
    sort_columns = _resolve_public_session_sort(sort_by, sort_dir)
    items = query.order_by(*sort_columns).offset(skip).limit(limit).all()

    payload_items: list[SessionListResponse] = []
    for item in items:
        item_payload = SessionListResponse.model_validate(item).model_dump(mode="python")
        item_payload["cover_image_url"] = _extract_cover_image_url(item)
        payload_items.append(SessionListResponse.model_validate(item_payload))

    return SessionPageResponse(
        items=payload_items,
        meta=PaginationMeta(
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + len(items)) < total,
        ),
    )


@router.get("/me", response_model=SessionPageResponse)
async def list_my_sessions(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=200, description="Maximum records to return"),
    title: str = Query(None, description="Optional title contains filter (case-insensitive)"),
    status: str = Query(
        None,
        description="Optional status filter - comma-separated (draft,published)",
    ),
    event_id: int = Query(None, description="Optional event ID filter"),
    sort_by: str = Query(
        "updated_at",
        description="Sort field: id, title, start_datetime, created_at, updated_at",
    ),
    sort_dir: str = Query("desc", description="Sort direction: asc or desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List sessions manageable by current user with pagination metadata."""
    from app.database.models import SessionStatus

    if sort_dir not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="Invalid sort_dir. Allowed: asc,desc")

    status_list = _validate_and_parse_enum_list(status, SessionStatus, "status") if status else None

    query = db.query(SessionModel).options(joinedload(SessionModel.location_rel))

    if not is_admin(current_user):
        query = query.outerjoin(Event, SessionModel.event_id == Event.id).filter(
            or_(
                SessionModel.owners.any(User.id == current_user.id),
                and_(SessionModel.event_id.is_not(None), Event.owner_id == current_user.id),
            )
        )

    if status_list:
        query = query.filter(SessionModel.status.in_(status_list))

    if title is not None and title.strip() != "":
        query = query.filter(SessionModel.title.ilike(f"%{title.strip()}%"))

    if event_id is not None:
        query = query.filter(SessionModel.event_id == event_id)

    total = query.with_entities(func.count(func.distinct(SessionModel.id))).scalar() or 0
    sort_columns = _resolve_session_sort(sort_by, sort_dir)
    items = query.order_by(*sort_columns).offset(skip).limit(limit).all()

    return SessionPageResponse(
        items=[SessionListResponse.model_validate(item) for item in items],
        meta=PaginationMeta(
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + len(items)) < total,
        ),
    )


@router.post("", response_model=SessionResponse, status_code=HTTP_201_CREATED)
async def create_session(
    session_in: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new session (requires authentication).

    - **title**: Session title (required)
    - **uri**: URL-safe identifier (required, must be unique)
    - **start_datetime**: Session start datetime (required)
    - **end_datetime**: Session end datetime (required, must be after start_datetime)
    - **speakers**: List of speakers (optional)
    - **tags**: List of tags (optional)
    - **short_description**: Short description (optional)
    - **location**: Session location (optional)
    - **recording_url**: Recording URL (optional)
    - **status**: Session status - draft or published (default: draft)
    - **session_format**: Format like input, lighting talk, diskussion, workshop, training, lab, other (optional, default: other)
    - **duration**: Duration in minutes (optional, or auto-calculated from times)
    - **language**: ISO 639-1 language code (default: en)
    - **event_id**: Associated event ID (optional)
    """
    # Check if URI already exists
    existing = session_crud.read_by_uri(db, session_in.uri)
    if existing:
        logger.warning("session_uri_conflict", uri=session_in.uri)
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"Session with URI '{session_in.uri}' already exists",
        )

    # Validate event exists and user owns it (if event_id provided)
    if session_in.event_id:
        event = event_crud.read(db, session_in.event_id)
        if not event:
            logger.warning("event_not_found_for_session", event_id=session_in.event_id)
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Event with ID {session_in.event_id} not found",
            )
        if event.owner_id != current_user.id:
            logger.warning(
                "auth_unauthorized_event_access_for_session",
                user_id=current_user.id,
                event_id=session_in.event_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: you do not own this event",
            )

    db_session = session_crud.create(db, session_in, initial_owner_user_id=current_user.id)
    return db_session


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: int,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get a session by ID. Published sessions are public; drafts visible only to owner."""
    db_session = session_crud.read(db, session_id)
    if not db_session:
        logger.warning("session_not_found", session_id=session_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Check access based on publication status
    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "session_access_denied",
            session_id=session_id,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return db_session


@router.get("/by-uri/{uri}", response_model=SessionResponse)
async def get_session_by_uri(
    uri: str,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get a session by URI. Published sessions are public; drafts visible only to owner."""
    db_session = session_crud.read_by_uri(db, uri)
    if not db_session:
        logger.warning("session_not_found_by_uri", uri=uri)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Check access based on publication status
    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "session_access_denied_by_uri",
            uri=uri,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return db_session


@router.get("/by-external-id/{label}/{external_id}", response_model=SessionResponse)
async def get_session_by_external_id(
    label: str,
    external_id: str,
    event_id: int | None = Query(None, description="Optional event scope"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get a session by labeled external ID. Published sessions are public."""
    db_session = session_crud.read_by_external_id(
        db, label=label, external_id=external_id, event_id=event_id
    )
    if not db_session:
        logger.warning(
            "session_not_found_by_external_id",
            label=label,
            external_id=external_id,
            event_id=event_id,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if not can_access_session_content(db_session, current_user):
        logger.warning(
            "session_access_denied_by_external_id",
            label=label,
            external_id=external_id,
            event_id=event_id,
            status=db_session.status,
            user_id=current_user.id if current_user else None,
        )
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return db_session


def _validate_and_parse_enum_list(value: str, enum_class, field_name: str) -> list[str] | None:
    """Validate and parse comma-separated enum values."""
    if not value:
        return None

    values_list = [v.strip() for v in value.split(",") if v.strip()]
    valid_values = [e.value for e in enum_class]

    for v in values_list:
        if v not in valid_values:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid {field_name} '{v}'. Allowed values: {', '.join(valid_values)}",
            )

    return values_list


@router.get(
    "",
    response_model=list[SessionListResponse],
    dependencies=[Depends(track_list_sessions_usage)],
)
async def list_sessions(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    status: str = Query(
        None, description="Filter by status - comma-separated (draft, published) - OR logic"
    ),
    event_id: int = Query(None, description="Filter by event ID"),
    session_format: str = Query(
        None,
        description="Filter by session format - comma-separated (input, lighting talk, diskussion, workshop, training, lab, other) - OR logic",
    ),
    tags: str = Query(None, description="Filter by tags (comma-separated, OR logic)"),
    location_cities: str | None = Query(
        None, description="Filter by city (comma-separated, OR logic)"
    ),
    location_names: str | None = Query(
        None,
        description="Filter by location name such as stage or room (comma-separated, OR logic)",
    ),
    language: str = Query(
        None,
        description="Filter by language - comma-separated (ISO 639-1 code, e.g., en,de) - OR logic",
    ),
    duration_min: int = Query(None, ge=0, description="Minimum duration in minutes"),
    duration_max: int = Query(None, ge=0, description="Maximum duration in minutes"),
    speaker: str = Query(None, description="Search for speaker name"),
    time_windows: str | None = Query(
        None,
        description='JSON array of time windows, e.g. [{"start":"2024-06-01T10:00:00","end":"2024-06-01T11:30:00"}]',
    ),
    search: str = Query(None, description="Full-text search on title, description, and speakers"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    List all sessions with advanced filtering and full-text search.

    Public users see only published sessions. Authenticated users also see their own drafts.

    **Filters (all optional):**
    - **skip**: Number of records to skip (default: 0)
    - **limit**: Maximum records to return (default: 100, max: 1000)
    - **status**: Filter by status - comma-separated (draft, published) - OR logic
    - **event_id**: Filter by event ID
    - **session_format**: Filter by session format - comma-separated (input, lighting talk, diskussion, workshop, training, lab, other) - OR logic
    - **tags**: Filter by tags - comma-separated list (OR logic: returns sessions with any tag)
    - **location_cities**: Filter by city - comma-separated list (OR logic)
    - **location_names**: Filter by location names (stage/room/venue) - comma-separated list (OR logic)
    - **language**: Filter by language code - comma-separated (e.g., en, de, fr) - OR logic
    - **duration_min**: Minimum duration in minutes
    - **duration_max**: Maximum duration in minutes
    - **speaker**: Search for speaker name (case-insensitive)
    - **time_windows**: JSON array of windows; sessions must fit completely inside at least one window
    - **search**: Full-text search on title, description, and speakers (case-insensitive)

    **Examples:**
    - `/api/v2/sessions?status=published&language=en`
    - `/api/v2/sessions?event_id=5&duration_min=20&duration_max=60`
    - `/api/v2/sessions?tags=ai,machine+learning&language=en,de`
    - `/api/v2/sessions?location_names=Landing:Stage+Berlin,AI:Stage+TU+Graz`
    - `/api/v2/sessions?location_cities=Berlin,Graz`
    - `/api/v2/sessions?tags=AI%26Technology,FutureSkills` (tags with ampersand - URL-encoded)
    - `/api/v2/sessions?search=machine+learning&status=published`
    - `/api/v2/sessions?session_format=input,workshop`
    - `/api/v2/sessions?time_windows=[{"start":"2024-06-01T10:00:00","end":"2024-06-01T11:30:00"}]` (sessions in timeframe)
    """
    from app.database.models import SessionFormat, SessionStatus

    # Validate and parse enum values using helper
    status_list = _validate_and_parse_enum_list(status, SessionStatus, "status") if status else None
    session_format_list = (
        _validate_and_parse_enum_list(session_format, SessionFormat, "session_format")
        if session_format
        else None
    )

    # Parse language (comma-separated, normalize to lowercase for consistency)
    language_list = None
    if language:
        language_list = [lang.strip().lower() for lang in language.split(",") if lang.strip()]

    # Parse locations (comma-separated)
    location_cities_list = None
    if location_cities:
        location_cities_list = [city.strip() for city in location_cities.split(",") if city.strip()]

    location_names_list = None
    if location_names:
        location_names_list = [name.strip() for name in location_names.split(",") if name.strip()]

    # Parse time windows JSON if provided
    parsed_time_windows = DateTimeUtils.parse_time_windows_json(time_windows)

    # Parse tags (comma-separated)
    tags_list = None
    if tags:
        tags_list = [t.strip() for t in tags.split(",") if t.strip()]

    # Use enhanced filtering
    sessions = session_crud.list_with_filters(
        db,
        skip=skip,
        limit=limit,
        status=status_list,
        event_id=event_id,
        session_format=session_format_list,
        tags=tags_list,
        location_cities=location_cities_list,
        location_names=location_names_list,
        language=language_list,
        duration_min=duration_min,
        duration_max=duration_max,
        speaker=speaker,
        time_windows=parsed_time_windows,
        search=search,
    )

    # Filter results: only include published sessions or user's own drafts
    filtered_sessions = [s for s in sessions if can_access_session_content(s, current_user)]

    return filtered_sessions


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: int,
    session_in: SessionUpdate,
    session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """
    Update a session partially (owner only).

    Only provide fields that need to be updated.
    """
    # Check URI conflict if URI is being updated
    if session_in.uri and session_in.uri != session.uri:
        existing = session_crud.read_by_uri(db, session_in.uri)
        if existing:
            logger.warning(
                "session_uri_conflict_on_update",
                session_id=session_id,
                uri=session_in.uri,
            )
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=f"Session with URI '{session_in.uri}' already exists",
            )

    # Validate event exists if event_id is being updated
    if session_in.event_id and session_in.event_id != session.event_id:
        event = event_crud.read(db, session_in.event_id)
        if not event:
            logger.warning("event_not_found_for_session_update", event_id=session_in.event_id)
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Event with ID {session_in.event_id} not found",
            )

    updated_session = session_crud.update(db, session_id, session_in)
    return updated_session


@router.delete("/{session_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    _: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Delete a session (owner only)."""
    session_crud.delete(db, session_id)
    return None


@router.get("/{session_id}/owners", response_model=list[SessionOwnerLinkResponse])
async def list_session_owners(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List owner links for a session (manageable users only)."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if not can_manage_session(session, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return session_crud.list_owners(db, session.id)


@router.post("/{session_id}/owners", response_model=SessionOwnerLinkResponse)
async def add_session_owner(
    session_id: int,
    payload: SessionOwnerAddRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a session owner (admin, event owner, or current session owner)."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if not can_manage_session(session, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    user = db.query(User).filter(User.id == payload.user_id).first()
    if user is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="User not found")

    owner_link = session_crud.add_owner(
        db,
        session_id=session.id,
        user_id=payload.user_id,
        added_by_user_id=current_user.id,
    )

    # Enrich with user info
    return {
        "session_id": owner_link.session_id,
        "user_id": owner_link.user_id,
        "user_name": user.username,
        "user_keycloak_id": getattr(user, "keycloak_sub", None),
        "email": user.email,
        "added_by_user_id": owner_link.added_by_user_id,
        "created_at": owner_link.created_at,
    }


@router.delete("/{session_id}/owners/{user_id}", status_code=HTTP_204_NO_CONTENT)
async def remove_session_owner(
    session_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a session owner (admin, event owner, or current session owner)."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if not can_manage_session(session, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    removed = session_crud.remove_owner(db, session.id, user_id)
    if not removed:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Owner link not found")

    return None


@router.post("/{session_id}/ownership-claims", response_model=SessionOwnershipClaimResponse)
async def request_session_ownership_claim(
    session_id: int,
    payload: SessionOwnershipClaimCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an ownership claim request for the authenticated user."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if session_crud.is_session_owner(db, session.id, current_user.id):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="User is already a session owner")

    claim = session_crud.create_ownership_claim(
        db,
        session_id=session.id,
        requester_user_id=current_user.id,
        request_note=payload.request_note,
    )

    # Enrich with user info
    requester = db.query(User).filter(User.id == current_user.id).first()
    return {
        "id": claim.id,
        "session_id": claim.session_id,
        "requester_user_id": claim.requester_user_id,
        "requester_email": requester.email if requester else None,
        "requester_name": requester.username if requester else None,
        "requester_keycloak_id": getattr(requester, "keycloak_sub", None) if requester else None,
        "status": claim.status,
        "request_note": claim.request_note,
        "review_note": claim.review_note,
        "reviewed_by_user_id": claim.reviewed_by_user_id,
        "reviewed_at": claim.reviewed_at,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
    }


@router.get("/{session_id}/ownership-claims", response_model=list[SessionOwnershipClaimResponse])
async def list_session_ownership_claims(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List ownership claims. Reviewers see all; requesters see own claims only."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Enrich claims with requester user data (keycloak id, username, email).
    rows = (
        db.query(
            SessionOwnershipClaim,
            User.username.label("requester_name"),
            User.email.label("requester_email"),
            User.keycloak_sub.label("requester_keycloak_id"),
        )
        .outerjoin(User, SessionOwnershipClaim.requester_user_id == User.id)
        .filter(SessionOwnershipClaim.session_id == session_id)
        .order_by(SessionOwnershipClaim.created_at.desc(), SessionOwnershipClaim.id.desc())
        .all()
    )

    enriched = [
        {
            "id": row[0].id,
            "session_id": row[0].session_id,
            "requester_user_id": row[0].requester_user_id,
            "requester_name": row[1],
            "requester_email": row[2],
            "requester_keycloak_id": row[3],
            "status": row[0].status,
            "request_note": row[0].request_note,
            "review_note": row[0].review_note,
            "reviewed_by_user_id": row[0].reviewed_by_user_id,
            "reviewed_at": row[0].reviewed_at,
            "created_at": row[0].created_at,
            "updated_at": row[0].updated_at,
        }
        for row in rows
    ]

    if can_manage_session(session, current_user, db):
        return enriched

    return [c for c in enriched if c["requester_user_id"] == current_user.id]


@router.get(
    "/ownership-claims/open",
    response_model=list[SessionOwnershipClaimSummaryResponse],
)
async def list_open_ownership_claims(
    event_id: int | None = Query(None, description="Optional event scope"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List pending ownership claims for sessions the current user can manage."""
    query = (
        db.query(
            SessionOwnershipClaim.id.label("claim_id"),
            SessionOwnershipClaim.session_id.label("session_id"),
            SessionModel.title.label("session_title"),
            SessionModel.event_id.label("event_id"),
            Event.title.label("event_title"),
            SessionOwnershipClaim.requester_user_id.label("requester_user_id"),
            User.email.label("requester_email"),
            User.username.label("requester_name"),
            User.keycloak_sub.label("requester_keycloak_id"),
            SessionOwnershipClaim.request_note.label("request_note"),
            SessionOwnershipClaim.created_at.label("created_at"),
        )
        .join(SessionModel, SessionOwnershipClaim.session_id == SessionModel.id)
        .outerjoin(Event, SessionModel.event_id == Event.id)
        .outerjoin(User, SessionOwnershipClaim.requester_user_id == User.id)
        .filter(SessionOwnershipClaim.status == SessionOwnershipClaimStatus.PENDING.value)
        .order_by(SessionOwnershipClaim.created_at.desc(), SessionOwnershipClaim.id.desc())
    )

    if event_id is not None:
        query = query.filter(SessionModel.event_id == event_id)

    if not is_admin(current_user):
        owner_exists = exists().where(
            and_(
                SessionOwner.session_id == SessionModel.id,
                SessionOwner.user_id == current_user.id,
            )
        )
        query = query.filter(
            or_(
                owner_exists,
                SessionModel.event.has(Event.owner_id == current_user.id),
            )
        )

    rows = query.all()

    return [
        {
            "claim_id": row.claim_id,
            "session_id": row.session_id,
            "session_title": row.session_title,
            "event_id": row.event_id,
            "event_title": row.event_title,
            "requester_user_id": row.requester_user_id,
            "requester_email": row.requester_email,
            "requester_name": row.requester_name,
            "requester_keycloak_id": row.requester_keycloak_id,
            "request_note": row.request_note,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post(
    "/{session_id}/ownership-claims/{claim_id}/approve",
    response_model=SessionOwnershipClaimResponse,
)
async def approve_session_ownership_claim(
    session_id: int,
    claim_id: int,
    payload: SessionOwnershipClaimReview,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Approve an ownership claim and add requester as session owner."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if not can_manage_session(session, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    claim = session_crud.read_ownership_claim(db, session.id, claim_id)
    if claim is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Claim not found")
    if claim.status != SessionOwnershipClaimStatus.PENDING.value:
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Claim already reviewed")

    # perform review and return enriched response

    # Enrich response with requester's keycloak id for cross-service notifications
    reviewed = session_crud.review_ownership_claim(
        db,
        claim=claim,
        reviewer_user_id=current_user.id,
        approve=True,
        review_note=payload.review_note,
    )

    requester = db.query(User).filter(User.id == reviewed.requester_user_id).one_or_none()
    return {
        "id": reviewed.id,
        "session_id": reviewed.session_id,
        "requester_user_id": reviewed.requester_user_id,
        "requester_name": getattr(requester, "username", None) if requester else None,
        "requester_email": getattr(requester, "email", None) if requester else None,
        "requester_keycloak_id": getattr(requester, "keycloak_sub", None) if requester else None,
        "status": reviewed.status,
        "request_note": reviewed.request_note,
        "review_note": reviewed.review_note,
        "reviewed_by_user_id": reviewed.reviewed_by_user_id,
        "reviewed_at": reviewed.reviewed_at,
        "created_at": reviewed.created_at,
        "updated_at": reviewed.updated_at,
    }


@router.post(
    "/{session_id}/ownership-claims/{claim_id}/reject",
    response_model=SessionOwnershipClaimResponse,
)
async def reject_session_ownership_claim(
    session_id: int,
    claim_id: int,
    payload: SessionOwnershipClaimReview,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reject an ownership claim without granting privileges."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if not can_manage_session(session, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    claim = session_crud.read_ownership_claim(db, session.id, claim_id)
    if claim is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Claim not found")
    if claim.status != SessionOwnershipClaimStatus.PENDING.value:
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail="Claim already reviewed")

    # perform review and return enriched response
    reviewed = session_crud.review_ownership_claim(
        db,
        claim=claim,
        reviewer_user_id=current_user.id,
        approve=False,
        review_note=payload.review_note,
    )

    requester = db.query(User).filter(User.id == reviewed.requester_user_id).one_or_none()
    return {
        "id": reviewed.id,
        "session_id": reviewed.session_id,
        "requester_user_id": reviewed.requester_user_id,
        "requester_name": getattr(requester, "username", None) if requester else None,
        "requester_email": getattr(requester, "email", None) if requester else None,
        "requester_keycloak_id": getattr(requester, "keycloak_sub", None) if requester else None,
        "status": reviewed.status,
        "request_note": reviewed.request_note,
        "review_note": reviewed.review_note,
        "reviewed_by_user_id": reviewed.reviewed_by_user_id,
        "reviewed_at": reviewed.reviewed_at,
        "created_at": reviewed.created_at,
        "updated_at": reviewed.updated_at,
    }


@router.get("/event/{event_id}/sessions", response_model=list[SessionListResponse])
async def list_event_sessions(
    event_id: int,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    List all sessions for a specific event.

    Public users see only published sessions. Authenticated users also see their own drafts.
    """
    # Verify event exists
    event = event_crud.read(db, event_id)
    if not event:
        logger.warning("event_not_found_for_session_list", event_id=event_id)
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Event not found")

    sessions = session_crud.list_by_event(db, event_id, skip=skip, limit=limit)

    # Filter results: only include published sessions or user's own drafts
    filtered_sessions = [s for s in sessions if can_access_session_content(s, current_user)]

    return filtered_sessions


@router.get("/documentation/rebuild-all")
async def rebuild_all_documentation(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Rebuild documentation artifacts for all published sessions that already have artifacts.

    Iterates over every published session and regenerates its documentation
    artifact. Useful after deploying changes to the artifact-building logic
    or to backfill sessions that were published before this feature existed.

    Requires authentication. Returns counts of rebuilt and failed sessions.
    """
    sessions = session_crud.list_with_filters(
        db,
        status=[SessionStatus.PUBLISHED.value],
        skip=0,
        limit=10000,
    )
    sessions = [s for s in sessions if s.published_documentation_artifact is not None]

    rebuilt = 0
    deleted = 0
    failed = 0

    for session in sessions:
        try:
            # Check if there is any generated content for the session
            contents = content_crud.list_for_session(db, session.id)
            if contents and len(contents) > 0:
                # Rebuild documentation only when generated content exists
                result = DocumentationBuilder.build_documentation(db, session.id)
                if result is not None:
                    rebuilt += 1
                else:
                    # Treat a None result as a failure to rebuild
                    failed += 1
            else:
                # No generated content: remove existing artifact (cleanup)
                session.published_documentation_artifact = None
                db.add(session)
                db.commit()
                db.refresh(session)
                deleted += 1
        except Exception as exc:
            logger.error(
                "rebuild_documentation_failed",
                session_id=session.id,
                error=str(exc),
            )
            failed += 1

    logger.info(
        "rebuild_all_documentation_complete",
        rebuilt=rebuilt,
        deleted=deleted,
        failed=failed,
    )

    return {"rebuilt": rebuilt, "deleted": deleted, "failed": failed, "total": len(sessions)}


@router.post("/{session_id}/documentation", response_model=SessionResponse)
async def create_session_documentation(
    session_id: int,
    _session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Build (or rebuild) a documentation artifact for a published session."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    if session.status != SessionStatus.PUBLISHED.value:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail="Documentation can only be created for published sessions",
        )

    artifact = DocumentationBuilder.build_documentation(db, session_id)
    if artifact is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    refreshed = session_crud.read(db, session_id)
    if refreshed is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    return refreshed


@router.delete("/{session_id}/documentation", response_model=SessionResponse)
async def delete_session_documentation(
    session_id: int,
    _session: SessionModel = Depends(require_session_owner),
    db: Session = Depends(get_db),
):
    """Delete existing documentation artifact from a session."""
    session = session_crud.read(db, session_id)
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    session.published_documentation_artifact = None
    db.add(session)
    db.commit()
    db.refresh(session)

    return session


@router.get("/{session_id}/documentation", response_model=SessionDocumentationResponse)
async def get_session_documentation(
    session_id: int,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    Get published documentation artifact for a session.

    Returns the pre-built JSON documentation containing session metadata
    and all generated content sections (summary, transcription, diagrams, etc.).

    - Published sessions: accessible to anyone
    - Draft sessions: only accessible to owner
    - Returns 404 if session not found or artifact not yet generated
    """
    session = session_crud.read(db, session_id)
    if not session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Verify access permissions
    if not can_access_session_content(session, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Return artifact if available
    if not session.published_documentation_artifact:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Documentation artifact not yet generated",
        )

    return session.published_documentation_artifact


@router.get("/by-uri/{uri}/documentation", response_model=SessionDocumentationResponse)
async def get_session_documentation_by_uri(
    uri: str,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    Get published documentation artifact for a session by URI.

    URI-based variant of documentation endpoint - useful for public-facing
    session pages that use clean URLs instead of database IDs.

    Returns 404 if session not found or if artifact not yet generated.
    """
    session = session_crud.read_by_uri(db, uri)
    if not session:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session not found")

    # Verify access permissions
    if not can_access_session_content(session, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Return artifact if available
    if not session.published_documentation_artifact:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Documentation artifact not yet generated",
        )

    return session.published_documentation_artifact
