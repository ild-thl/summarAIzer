"""Short description step - generates optimized concise descriptions for embedding."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.crud.session import session_crud
from app.database.models import Session as SessionModel
from app.schemas.session import SessionUpdate
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.llm_step import LLMStep

logger = structlog.get_logger()


class ShortDescriptionStep(LLMStep):
    """
    Generates optimized short descriptions (150-250 chars) for better semantic embeddings.

    Input: Session with existing short_description
    Output: Concise, keyword-dense description optimized for 768-token embedding model

    Overwrites session.short_description. Because short_description is in
    EMBEDDING_REFRESH_FIELDS, calling session_crud.update() automatically triggers
    embedding re-generation.

    Independent step (no dependencies) - can run in parallel with other steps.
    Does not require transcription.
    """

    # Configuration - can be overridden
    min_description_length: int = 20
    max_description_length: int = 250  # Target length (soft limit)
    hard_max_length: int = 350  # Absolute maximum before truncation (hard limit with margin)
    temperature: float = 0.3  # Low for factual compression
    max_tokens: int = 300  # Give LLM room to stay within margin
    top_p: float = 0.9  # Moderate for some variability in phrasing
    model_name: str = "gemma-3-27b-it"  # Model with good context handling for compression

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "short_description"

    @property
    def context_requirements(self) -> list[str]:
        """No required context keys - can run independently in parallel with other steps."""
        return []

    def get_model_config(self) -> ChatModelConfig:
        """Configuration for description optimization - low temperature for consistency."""
        return ChatModelConfig(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            top_p=self.top_p,
        )

    async def _validate_and_prepare_context(
        self, session_id: int, db: Session, _: dict[str, Any]
    ) -> None:
        """
        Validate session has description before processing.

        Override to add custom validation for description length.
        LLMStep base already validates context_requirements.

        Args:
            session_id: Session ID
            db: Database session
            context: Execution context (unused, no required context keys)

        Raises:
            ValueError: If session not found or description too short
        """
        # First call parent validation (will validate context_requirements)
        await super()._validate_and_prepare_context(session_id, db, _)

        # Then add custom validation for description
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not session:
            raise ValueError(f"Session {session_id} not found")

        source = session.short_description or ""
        if len(source) < self.min_description_length:
            logger.error(
                "short_description_too_short",
                session_id=session_id,
                current_length=len(source),
                min_required=self.min_description_length,
            )
            raise ValueError(
                f"Session {session_id} short_description too short or missing "
                f"(current: {len(source)} chars, minimum: {self.min_description_length} chars)"
            )

        logger.info(
            "short_description_validation_passed",
            session_id=session_id,
            description_length=len(source),
        )

    def get_messages(self, session: SessionModel, _: dict[str, Any]) -> list[BaseMessage]:
        """
        Build LLM messages for description optimization.

        LLMStep interface: get_messages(session, context).
        Note: Unlike most steps, we don't need context parameter as all data comes from session.

        Args:
            session: Session model with title, speakers, tags
            _: Execution context (unused for message building)

        Returns:
            List of LangChain messages
        """
        # Note: This is called from _invoke_and_process in LLMStep
        # We don't have direct access to session here, so we'll handle this
        # by loading session in _invoke_and_process override
        raise NotImplementedError(
            "ShortDescriptionStep overrides _invoke_and_process() instead of get_messages()"
        )

    def process_response(self, response: Any) -> dict[str, Any]:
        """
        Process LLM response and enforce length limits with margin.

        Args:
            response: LLM response from LangChain

        Returns:
            Dict with optimized content and metadata
        """
        optimized = response.content if hasattr(response, "content") else str(response)
        optimized = optimized.strip()

        # Enforce hard maximum length only if significantly exceeded
        # (allows LLM flexibility within margin between max_description_length and hard_max_length)
        if len(optimized) > self.hard_max_length:
            logger.warning(
                "description_exceeds_hard_limit",
                received_length=len(optimized),
                hard_max_length=self.hard_max_length,
                truncating=True,
            )
            optimized = optimized[: self.hard_max_length].rstrip()
        elif len(optimized) > self.max_description_length:
            # Within margin - acceptable, but log for monitoring
            logger.info(
                "description_exceeds_soft_max_within_margin",
                received_length=len(optimized),
                soft_max=self.max_description_length,
                hard_max=self.hard_max_length,
                margin=self.hard_max_length - self.max_description_length,
            )

        # Warn if too short
        if len(optimized) < self.min_description_length:
            logger.warning(
                "description_below_minimum",
                received_length=len(optimized),
                min_length=self.min_description_length,
            )

        return {
            "content": optimized,
            "content_type": "plain_text",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "optimized_short_description",
                "length": len(optimized),
                "min_length": self.min_description_length,
                "soft_max_length": self.max_description_length,
                "hard_max_length": self.hard_max_length,
            },
        }

    async def _invoke_and_process(self, session: SessionModel, _: dict[str, Any]) -> dict[str, Any]:
        """
        Override LLM invocation to include session data in messages.

        ShortDescriptionStep needs session metadata (speakers, tags) for message building,
        unlike standard LLMStep steps. So we customize the invocation pattern.

        Args:
            session: Session model
            context: Execution context

        Returns:
            Processed LLM response
        """
        # Build messages using session data
        source = session.short_description or ""
        messages = self._build_messages_for_session(session, source)

        # Invoke LLM
        model = self.get_model()
        response = await model.ainvoke(messages)

        logger.debug(
            "short_description_step_llm_invoked",
            step=self.identifier,
            session_id=session.id,
            model=self.get_model_config().model,
        )

        # Process response
        result = self.process_response(response)

        logger.info(
            "short_description_generation_completed",
            session_id=session.id,
            original_length=len(source),
            optimized_length=len(result["content"]),
        )

        return result

    def _build_messages_for_session(self, session: SessionModel, source: str) -> list[BaseMessage]:
        """
        Build LLM messages for description optimization using session metadata.

        Helper method used in _invoke_and_process override.

        Args:
            session: Session model with metadata
            source: Original description text

        Returns:
            List of LangChain messages
        """
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"
        tags = ", ".join(session.tags) if session.tags else "General"

        return [
            SystemMessage(
                content="""Du bist Spezialist:in für präzise, embedding-optimierte Veranstaltungsbeschreibungen.

Deine Aufgabe ist es, eine vorhandene (möglicherweise lange) Veranstaltungsbeschreibung in eine sehr kurze,
keyword-dichte Zusammenfassung (150-250 Zeichen) umzuwandeln.

Anforderungen:
- **Länge**: 150-250 Zeichen (≈ 30-50 Wörter)
- **Struktur**: [Domäne/Thema] + [Was/Wie] + [Zielgruppe/Outcome]
- **Keywords**: Dicht mit Fachbegriffen, Technologien, Framework-Namen, konkreten Methoden
- **Stil**: Neutral, Präsens, keine Füllwörter ("In dieser Session...", "Lernen Sie...")
- **Fidelität**: NUR Information aus der Quelle verwenden, KEINE Halluzinationen
- **Auslassungen**: Keine generischen Beschreibungen, bewahre konkrete Details (Tool-Namen, Zahlen)

Gib AUSSCHLIESSLICH die optimierte Beschreibung zurück, nichts anderes."""
            ),
            HumanMessage(
                content=f"""Veranstaltungstitel: {session.title}
Referent:innen: {speakers}
Tags: {tags}

Originalbeschreibung:
{source}

Erstelle nun eine optimierte, embedding-freundliche Kurzbeschreibung (150-250 Zeichen):"""
            ),
        ]

    def _save_to_db(
        self,
        db: Session,
        session_id: int,
        execution_id: int,
        identifier: str,
        content: dict[str, Any],
    ) -> None:
        """
        Save optimized description and trigger embedding refresh.

        Extends parent behavior:
        1. Save to GeneratedContent table (identifier='short_description')
        2. Update session.short_description with optimized text
        3. Trigger embedding refresh via session_crud.update()

        Args:
            db: SQLAlchemy database session
            session_id: Session ID
            execution_id: WorkflowExecution ID for tracking
            identifier: Step identifier ('short_description')
            content: Dict with optimized description content
        """
        # Call parent to save to GeneratedContent table
        super()._save_to_db(db, session_id, execution_id, identifier, content)

        # Now update session.short_description with optimized text
        try:
            optimized_text = content.get("content", "")

            if optimized_text:
                # Use session_crud.update() to trigger embedding refresh
                # (short_description is in EMBEDDING_REFRESH_FIELDS)
                update_data = SessionUpdate(short_description=optimized_text)
                updated_session = session_crud.update(db, session_id, update_data)

                if updated_session:
                    logger.info(
                        "session_short_description_updated",
                        session_id=session_id,
                        execution_id=execution_id,
                        new_length=len(optimized_text),
                    )
                else:
                    logger.warning(
                        "session_not_found_for_description_update",
                        session_id=session_id,
                        execution_id=execution_id,
                    )

        except Exception as e:
            logger.error(
                "failed_to_update_session_short_description",
                session_id=session_id,
                execution_id=execution_id,
                error=str(e),
                exc_info=True,
            )
            # Don't raise - content was already saved to generated_content table


# Auto-register this step when imported
_short_description_step = ShortDescriptionStep()
StepRegistry.register(_short_description_step)
