"""Service for building published session documentation artifacts."""

import logging
from collections import OrderedDict
from datetime import datetime

from sqlalchemy.orm import Session as SQLSession

from app.crud.generated_content import list_all_for_session
from app.crud.session import session_crud
from app.database.models import SessionStatus
from app.schemas.session import DocumentationSection, SessionDocumentationResponse

logger = logging.getLogger(__name__)

TRANSCRIPTION_IDENTIFIER = "transcription"


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
            # Load session
            session = session_crud.read(db, session_id)
            if not session:
                logger.warning(f"Session {session_id} not found for documentation build")
                return None

            # Verify published status
            if session.status != SessionStatus.PUBLISHED:
                logger.warning(
                    f"Session {session_id} not published (status={session.status}), skipping documentation build"
                )
                return None

            # Load all generated content for the session
            content_items = list_all_for_session(db, session_id)

            # Keep only the most recent item for each identifier to avoid duplicate sections.
            latest_by_identifier: OrderedDict[str, object] = OrderedDict()
            for content in content_items:
                latest_by_identifier[content.identifier] = content

            latest_items = sorted(
                latest_by_identifier.values(),
                key=lambda item: (item.created_at or datetime.min, item.id),
            )

            # Transform the deduplicated set into documentation sections.
            sections: list[DocumentationSection] = []
            for idx, content in enumerate(latest_items):
                section_type = content.content_type
                section_content = content.content
                section_resource_url = None

                if content.identifier == TRANSCRIPTION_IDENTIFIER:
                    # Avoid embedding very large transcription blobs in the artifact payload.
                    section_type = "resource_link"
                    section_content = f"/sessions/{session.id}/content/{TRANSCRIPTION_IDENTIFIER}"

                section = DocumentationSection(
                    identifier=content.identifier,
                    type=section_type,
                    title=_get_section_title(content.identifier),
                    content=section_content,
                    resource_url=section_resource_url,
                    order=idx,
                    source=content.meta_info.get("source") if content.meta_info else None,
                    meta=content.meta_info,
                )
                sections.append(section)

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
                doc_version="1.0",
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
