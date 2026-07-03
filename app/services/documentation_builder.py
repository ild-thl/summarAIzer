"""Service for building published session documentation artifacts."""

import json
from collections import OrderedDict
from datetime import datetime
from urllib.parse import quote, urlparse

import structlog
from sqlalchemy.orm import Session as SQLSession

from app.config.settings import get_settings
from app.crud.generated_content import list_for_session
from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.schemas.session import DocumentationSection, SessionDocumentationResponse
from app.services.s3_service import get_s3_service

logger = structlog.get_logger()

TRANSCRIPTION_IDENTIFIER = "transcription"
SLIDE_DECK_IDENTIFIER = "slide_deck"
URL_SECTION_TYPES = {"resource_link", "image", "image_url"}
settings = get_settings()


class DocumentationBuilder:
    """Builds and persists published session documentation artifacts."""

    @staticmethod
    def build_documentation(db: SQLSession, session_id: int) -> dict | None:
        """
        Build and persist published documentation artifact for a session.

        This is called explicitly by API actions for published sessions.
        The artifact is a versioned JSON object stored in the session record
        containing all available generated content sections plus core metadata.

        Args:
            db: Database session
            session_id: ID of session to document

        Returns:
            dict: Serialized SessionDocumentationResponse, or None if session not found or not published

        Raises:
            None (logs errors and returns None on failure)
        """
        try:
            session = DocumentationBuilder._get_published_session(db, session_id)
            if session is None:
                return None

            sections, contains_ai_generated_content, all_ai_content_editorially_reviewed = (
                DocumentationBuilder._build_sections_and_ai_flags(db, session)
            )

            # Build response with core session metadata
            now = datetime.utcnow()
            response = SessionDocumentationResponse(
                id=session.id,
                event_id=session.event_id,
                title=session.title,
                speakers=session.speakers,
                tags=session.tags,
                description=session.description,
                short_description=session.short_description,
                location=session.location_rel,
                start_datetime=session.start_datetime,
                end_datetime=session.end_datetime,
                duration=session.duration,
                language=session.language,
                uri=session.uri,
                session_format=session.session_format.value if session.session_format else None,
                recording_url=session.recording_url,
                sections=sections,
                contains_ai_generated_content=contains_ai_generated_content,
                all_ai_content_editorially_reviewed=all_ai_content_editorially_reviewed,
                doc_version="1.1",
                generated_at=now,
                updated_at=now,
            )

            # Persist artifact in session record
            session.published_documentation_artifact = response.model_dump(mode="json")
            db.commit()

            logger.info(
                f"Built and persisted documentation artifact for session {session_id} "
                f"with {len(sections)} sections"
            )

            return response.model_dump(mode="json")

        except Exception as e:
            logger.error(
                f"Error building documentation for session {session_id}: {e}", exc_info=True
            )
            return None

    @staticmethod
    def _get_published_session(db: SQLSession, session_id: int):
        """Return session only when it exists and is published."""
        session = session_crud.read(db, session_id)
        if not session:
            logger.warning(f"Session {session_id} not found for documentation build")
            return None

        if session.status != SessionStatus.PUBLISHED:
            logger.warning(
                f"Session {session_id} not published (status={session.status}), skipping documentation build"
            )
            return None

        return session

    @staticmethod
    def _build_sections_and_ai_flags(db: SQLSession, session):
        """Create documentation sections and aggregate AI review flags."""
        content_items = list_for_session(db, session.id)

        # Deduplicate - keep only latest per identifier (by creation time)
        deduped: OrderedDict[str, any] = OrderedDict()
        for content in content_items:
            deduped[content.identifier] = content

        sections: list[DocumentationSection] = []
        ai_section_count = 0
        ai_reviewed_count = 0

        for idx, content in enumerate(deduped.values()):
            section = DocumentationBuilder._build_section(session.id, content, idx)
            sections.append(section)

            if section.ai_generated:
                ai_section_count += 1
                if section.editorially_reviewed:
                    ai_reviewed_count += 1

        contains_ai_generated_content = ai_section_count > 0
        all_ai_content_editorially_reviewed = (
            ai_section_count > 0 and ai_reviewed_count == ai_section_count
        )
        return sections, contains_ai_generated_content, all_ai_content_editorially_reviewed

    @staticmethod
    def _build_section(session_id: int, content, order: int) -> DocumentationSection:
        """Transform a generated content row into a documentation section."""
        section_type = content.content_type
        section_content = content.content
        section_resource_url = None
        section_embed_url = None

        if content.identifier == TRANSCRIPTION_IDENTIFIER:
            # Avoid embedding very large transcription blobs in the artifact payload.
            section_type = "resource_link"
            section_resource_url = (
                f"{settings.api_base_url.rstrip('/')}/api/v2/sessions/{session_id}/content/"
                f"{TRANSCRIPTION_IDENTIFIER}"
            )
            section_content = None
        elif content.identifier == SLIDE_DECK_IDENTIFIER:
            section_type = "resource_link"
            # Delegate slide-specific logic to helper
            section_resource_url, section_embed_url = _build_slide_deck_section(session_id, content)
            section_content = None
        elif section_type in URL_SECTION_TYPES:
            section_resource_url = _extract_resource_url(content.content, content.meta_info)
            section_content = None

            if section_resource_url is None:
                logger.warning(
                    "Dropping invalid URL content from documentation section",
                    extra={
                        "session_id": session_id,
                        "identifier": content.identifier,
                        "content_type": section_type,
                    },
                )
            else:
                section_resource_url = _maybe_copy_s3_for_publication(
                    session_id, content, section_resource_url
                )

        return DocumentationSection(
            identifier=content.identifier,
            type=section_type,
            title=_get_section_title(content.identifier),
            content=section_content,
            resource_url=section_resource_url,
            embed_url=section_embed_url,
            order=order,
            source=content.meta_info.get("source") if content.meta_info else None,
            ai_generated=bool(content.ai_generated),
            editorially_reviewed=bool(content.editorially_reviewed),
            meta=content.meta_info,
        )


def _get_section_title(identifier: str) -> str:
    """Convert identifier to human-readable title."""
    title_map = {
        "summary": "Summary",
        "key_takeaways": "Key Takeaways",
        "qna": "Audience Q&A",
        "glossary": "Concept Glossary",
        "diagram": "Diagram",
        "transcription": "Transcription",
        "tags": "Tags",
        "key_points": "Key Points",
        "next_steps": "Next Steps",
        "questions": "Questions",
    }
    return title_map.get(identifier, identifier.replace("_", " ").title())


def _extract_resource_url(content: str | None, meta_info: dict | None) -> str | None:
    """Extract canonical URL for URL-based documentation sections."""
    candidates = _collect_resource_url_candidates(content, meta_info)

    for candidate in candidates:
        url = candidate.strip()
        if _is_http_url(url):
            return url

    return None


def _collect_resource_url_candidates(content: str | None, meta_info: dict | None) -> list[str]:
    """Collect all possible URL candidate strings from content and metadata."""
    candidates: list[str] = []

    if isinstance(content, str):
        candidates.append(content)

        payload = _parse_json_dict(content)
        if payload:
            candidates.extend(_extract_url_candidates_from_mapping(payload))

    if isinstance(meta_info, dict):
        candidates.extend(_extract_url_candidates_from_mapping(meta_info))

    return candidates


def _extract_url_candidates_from_mapping(payload: dict) -> list[str]:
    """Extract direct URL fields and optional S3-derived URL from a mapping."""
    candidates: list[str] = []

    for key in ["resource_url", "image_url", "url"]:
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(value)

    s3_key = payload.get("s3_key")
    if isinstance(s3_key, str):
        s3_url = _build_public_s3_url(s3_key)
        if s3_url:
            candidates.append(s3_url)

    return candidates


def _parse_json_dict(content: str) -> dict | None:
    """Parse a JSON object string and return dict when valid."""
    candidate = content.strip()
    if not candidate.startswith("{"):
        return None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _build_public_s3_url(s3_key: str) -> str | None:
    """Build full S3 URL from key when AWS_URL is configured."""
    base = (settings.aws_url or "").rstrip("/")
    key = s3_key.strip().lstrip("/")
    if not base or not key:
        return None
    return f"{base}/{key}"


def _is_http_url(value: str) -> bool:
    """Return True when value is an absolute HTTP(S) URL."""
    if value == "":
        return False

    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _maybe_copy_s3_for_publication(session_id: int, content, section_resource_url: str) -> str:
    """If the provided URL or content meta references an S3 key in our bucket,
    copy it into a stable published prefix and return the rewritten public URL.

    This is best-effort: failures are logged but do not stop documentation build.
    """
    try:
        aws_base = (settings.aws_url or "").rstrip("/")
        s3 = get_s3_service()

        # Prefer explicit meta_info.s3_key when present
        meta_s3_key = None
        if isinstance(content.meta_info, dict):
            meta_s3_key = content.meta_info.get("s3_key")

        source_key = None
        if isinstance(meta_s3_key, str) and meta_s3_key.strip():
            source_key = meta_s3_key.strip().lstrip("/")
        else:
            if aws_base and section_resource_url.startswith(aws_base + "/"):
                source_key = section_resource_url[len(aws_base) + 1 :].lstrip("/")

        if not source_key:
            return section_resource_url
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        basename = source_key.rsplit("/", 1)[-1]
        dest_key = f"content/summaraizer/published/session_{session_id}/{ts}/{basename}"

        try:
            s3.copy_object(source_key, dest_key, acl="public-read")
            new_url = _build_public_s3_url(dest_key) or section_resource_url
            if isinstance(content.meta_info, dict):
                content.meta_info["s3_key"] = dest_key
                if "resource_url" in content.meta_info:
                    content.meta_info["resource_url"] = new_url
            return new_url
        except Exception:
            logger.exception(
                "failed_to_copy_s3_object_for_publication",
                session_id=session_id,
                source=source_key,
            )
            return section_resource_url

    except Exception:
        logger.exception(
            "error_during_publish_copy_check",
            session_id=session_id,
            identifier=getattr(content, "identifier", None),
        )
        return section_resource_url


def _build_slide_deck_section(session_id: int, content) -> tuple[str | None, str | None]:
    """Build resource/embed URLs for a slide_deck section, copying S3 object when possible.

    Returns (resource_url, embed_url). Falls back to API endpoints when copy not possible.
    """
    api_download = (
        f"{settings.api_base_url.rstrip('/')}/api/v2/sessions/{session_id}/slide-files/download"
    )
    api_embed = (
        f"{settings.api_base_url.rstrip('/')}/api/v2/sessions/{session_id}/slide-files/embed"
    )
    try:
        # Prefer explicit s3_key present inside the stored content payload
        payload = _parse_json_dict(content.content) if isinstance(content.content, str) else None
        if payload and isinstance(payload.get("s3_key"), str):
            s3_src = _build_public_s3_url(payload.get("s3_key"))
            if s3_src:
                published_url = _maybe_copy_s3_for_publication(session_id, content, s3_src)
                if published_url and published_url != api_download:
                    s3_key = None
                    if isinstance(content.meta_info, dict):
                        s3_key = content.meta_info.get("s3_key")
                    if s3_key:
                        return published_url, f"{api_embed}?s3_key={quote(s3_key)}"
                    return published_url, api_embed

    except Exception:
        logger.exception("slide_publish_copy_failed", session_id=session_id)

    return api_download, api_embed
