"""YouTube subtitle scraping transcription provider."""

import structlog
from sqlalchemy.orm import Session as SQLSession

from app.services.transcription.exceptions import TranscriptionUnavailableError

logger = structlog.get_logger()


class YouTubeTranscriptionProvider:
    """
    Fetches transcription from YouTube auto-generated subtitles.

    Expects `context["youtube_url"]` or the session to have a `recording_url`.
    """

    def can_handle(self, session_id: int, db: SQLSession, context: dict) -> bool:
        """Return True if a YouTube URL is available in the context."""
        return bool(self._extract_url(session_id, db, context))

    def transcribe(self, session_id: int, db: SQLSession, context: dict) -> str:
        """
        Fetch YouTube subtitles and return concatenated transcript text.

        Raises:
            TranscriptionUnavailableError: If no subtitles can be found.
        """
        from youtube_transcript_api import (  # lazy import — optional dep
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
            YouTubeTranscriptApi,
        )
        from youtube_transcript_api.formatters import TextFormatter

        url = self._extract_url(session_id, db, context)
        if not url:
            raise TranscriptionUnavailableError(
                f"No YouTube URL available for session {session_id}"
            )

        video_id = self._parse_video_id(url)
        if not video_id:
            raise TranscriptionUnavailableError(f"Could not extract video ID from URL: {url!r}")

        logger.info(
            "youtube_transcription_fetching",
            session_id=session_id,
            video_id=video_id,
        )

        try:
            ytt_api = YouTubeTranscriptApi()
            transcript_list = ytt_api.list(video_id)
            # Prefer manually created; fall back to auto-generated
            try:
                transcript = transcript_list.find_manually_created_transcript(
                    ["de", "en", "fr", "es", "it"]
                )
            except NoTranscriptFound:
                transcript = transcript_list.find_generated_transcript(
                    ["de", "en", "fr", "es", "it"]
                )

            entries = transcript.fetch()
            formatter = TextFormatter()
            text = formatter.format_transcript(entries)

            logger.info(
                "youtube_transcription_fetched",
                session_id=session_id,
                video_id=video_id,
                char_count=len(text),
            )
            return text

        except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as exc:
            raise TranscriptionUnavailableError(
                f"YouTube transcript unavailable for video {video_id}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_url(self, session_id: int, db: SQLSession, context: dict) -> str | None:
        """Extract YouTube URL from context or session model."""
        # 1. Direct context key
        url = context.get("youtube_url")
        if url:
            return url

        # 2. Session model recording_url (if it's a YouTube URL)
        from app.crud.session import session_crud

        session = session_crud.read(db, session_id)
        if session and session.recording_url and self._parse_video_id(session.recording_url):
            return session.recording_url

        return None

    @staticmethod
    def _parse_video_id(url: str) -> str | None:
        """Extract YouTube video ID from a URL."""
        import re

        patterns = [
            r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
            r"(?:embed/)([A-Za-z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
