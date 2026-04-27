"""TranscriptionStep — fetches or generates transcription and persists it."""

from typing import Any

import structlog
from sqlalchemy.orm import Session as SQLSession

from app.services.transcription.exceptions import (
    TranscriptionPendingError,
)
from app.services.transcription.service import get_transcription
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.base_step import WorkflowStep

logger = structlog.get_logger()


class TranscriptionStep(WorkflowStep):
    """
    Fetches transcription for a session (YouTube subtitles or Whisper) and persists it.

    If a transcription already exists in context it is skipped (no-op).
    If audio files are pending/processing, raises TranscriptionPendingError
    which will cause the workflow to fail immediately.
    If no transcription source is available, raises ValueError so the workflow fails.
    """

    @property
    def identifier(self) -> str:
        return "transcription"

    @property
    def context_requirements(self) -> list[str]:
        return []  # No prior steps required

    async def execute(
        self,
        session_id: int,
        execution_id: int,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Fetch and persist transcription, or skip if already present.

        Overrides WorkflowStep.execute to avoid calling _generate/_save_to_db
        when skipping is needed.
        """
        # Skip if transcription already in context (pre-loaded in execute_generated_content)
        if context.get("transcription"):
            logger.info(
                "transcription_step_skipped_already_present",
                session_id=session_id,
                execution_id=execution_id,
            )
            return {"transcription": context["transcription"]}

        return await super().execute(session_id, execution_id, context)

    async def _generate(
        self, session_id: int, db: SQLSession, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Invoke the transcription service and return structured content."""
        try:
            text = get_transcription(session_id, db, context)
        except TranscriptionPendingError:
            # Re-raise so the workflow fails with a clear message
            raise

        if not text:
            raise ValueError(
                f"No transcription source available for session {session_id}. "
                "Upload audio files or provide a YouTube URL."
            )

        logger.info(
            "transcription_step_generated",
            session_id=session_id,
            char_count=len(text),
        )

        return {
            "content": text,
            "content_type": "plain_text",
            "meta_info": {"source": "auto_transcription"},
        }


# Auto-register step
_transcription_step = TranscriptionStep()
StepRegistry.register(_transcription_step)
