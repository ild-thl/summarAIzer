"""Metadata mapping helpers for embedding entities."""

from __future__ import annotations

from typing import Any


class EmbeddingMetadataBuilder:
    """Utility methods for building Chroma metadata payloads."""

    @staticmethod
    def build_session_metadata(session: Any) -> dict[str, Any]:
        """Build metadata for session entities using existing Chroma schema."""
        return {
            "title": session.title,
            "status": session.status.value if session.status else None,
            "event_id": session.event_id if session.event_id else None,
            "session_format": session.session_format.value if session.session_format else None,
            "tags": session.tags or None,
            "language": session.language or None,
            "location_city": (session.location_rel.city if session.location_rel else None),
            "location_name": (session.location_rel.name if session.location_rel else None),
            "duration": session.duration if session.duration else None,
            "speakers": session.speakers or None,
            "start_datetime": (
                session.start_datetime.timestamp() if session.start_datetime else None
            ),
            "end_datetime": session.end_datetime.timestamp() if session.end_datetime else None,
        }
