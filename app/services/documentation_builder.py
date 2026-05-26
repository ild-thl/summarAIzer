"""Service for building published session documentation artifacts."""

import logging
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session as SQLSession

from app.config.settings import get_settings
from app.crud.generated_content import list_for_session
from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.schemas.session import DocumentationSection, SessionDocumentationResponse

logger = logging.getLogger(__name__)

TRANSCRIPTION_IDENTIFIER = "transcription"
URL_SECTION_TYPES = {"resource_link", "image", "image_url"}
settings = get_settings()


class DocumentationBuilder:
    """Builds and persists published session documentation artifacts."""

    @staticmethod
    def build_documentation(db: SQLSession, session_id: int) -> dict | None:
        """
        Build and persist published documentation artifact for a session.

        This is called when a session transitions to PUBLISHED status.
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

        if content.identifier == TRANSCRIPTION_IDENTIFIER:
            # Avoid embedding very large transcription blobs in the artifact payload.
            section_type = "resource_link"
            section_resource_url = (
                f"{settings.api_base_url.rstrip('/')}/api/v2/sessions/{session_id}/content/"
                f"{TRANSCRIPTION_IDENTIFIER}"
            )
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

        return DocumentationSection(
            identifier=content.identifier,
            type=section_type,
            title=_get_section_title(content.identifier),
            content=section_content,
            resource_url=section_resource_url,
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
    candidates = []
    if isinstance(content, str):
        candidates.append(content)

    if isinstance(meta_info, dict):
        for key in ["resource_url", "image_url", "url"]:
            value = meta_info.get(key)
            if isinstance(value, str):
                candidates.append(value)

    for candidate in candidates:
        url = candidate.strip()
        if _is_http_url(url):
            return url

    return None


def _is_http_url(value: str) -> bool:
    """Return True when value is an absolute HTTP(S) URL."""
    if value == "":
        return False

    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
