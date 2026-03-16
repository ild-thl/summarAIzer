"""Template base class for prompt-based workflow steps."""

from abc import abstractmethod
from typing import Any, Dict, List

import structlog
from langchain_core.messages import BaseMessage
from sqlalchemy.orm import Session

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.steps.base_step import WorkflowStep

logger = structlog.get_logger()


class PromptTemplate(WorkflowStep):
    """
    Base class for workflow steps that use LLM prompts.

    Prompts are integral to the WorkflowStep logic, not separate components.
    Encapsulates common pattern:
    - Load session data
    - Prepare inputs with context injection
    - Generate LLM messages
    - Invoke LLM
    - Process response
    - Return structured output

    Subclasses implement:
    - get_messages(): Create LangChain messages with context payload injection
    - process_response(): Transform LLM response into return dict
    """

    @abstractmethod
    def get_messages(self, session: SessionModel, context: Dict[str, Any]) -> List[BaseMessage]:
        """
        Generate LLM messages for this step with context injection.

        Combines prompt design with context payload substitution in one method.
        Returns a list of LangChain BaseMessage objects (typically SystemMessage + HumanMessage).

        Args:
            session: Database session model with event/session metadata
            context: Generation context from workflow state (prior step results + transcription)

        Returns:
            List of LangChain BaseMessage objects ready for LLM invocation
        """
        pass

    @abstractmethod
    def process_response(self, response: Any) -> Dict[str, Any]:
        """
        Process LLM response into step output.

        Args:
            response: Response from LLM model

        Returns:
            Dict with keys: content, content_type, meta_info
        """
        pass

    async def _invoke_and_process(
        self, session: SessionModel, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Invoke LLM and process response.

        This method can be overridden by subclasses to implement custom generation logic.
        Default behavior: Call LLM with messages, then process response.

        Args:
            session: Session model with metadata
            context: Workflow context with prior results

        Returns:
            Processed response dict with content, content_type, meta_info
        """
        # Generate messages with context injection
        messages = self.get_messages(session, context)

        # Call LLM
        response = await self.get_model().ainvoke(messages)

        logger.debug(
            "prompt_step_llm_invoked",
            step=self.identifier,
            session_id=session.id,
            model=self.get_model_config().model,
        )

        # Process response
        result = self.process_response(response)

        return result

    async def _generate(
        self, session_id: int, db: Session, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute prompt-based generation.

        Common flow for all prompt steps:
        1. Validate inputs
        2. Load session
        3. Generate messages with context injection
        4. Call LLM
        5. Process response

        Subclasses can override _invoke_and_process() for custom LLM invocation patterns.
        """
        try:
            # Validate db parameter
            if not isinstance(db, Session):
                db_type = type(db).__name__
                db_value = repr(db)[:100]
                logger.error(
                    "invalid_db_parameter_type",
                    step=self.identifier,
                    session_id=session_id,
                    db_type=db_type,
                    db_value=db_value,
                    expected_type="sqlalchemy.orm.Session",
                )
                raise TypeError(
                    f"Expected SQLAlchemy Session for 'db' parameter, got {db_type}. "
                    f"Value: {db_value}. This typically means the database session was serialized "
                    f"or incorrectly passed through the workflow state."
                )

            # Load session
            session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not session:
                raise ValueError(f"Session {session_id} not found")

            # Get transcription from context (most prompts need it)
            if "transcription" not in context:
                raise ValueError("No transcription found in context")

            # Invoke LLM and process (can be overridden for custom behavior)
            result = await self._invoke_and_process(session, context)

            logger.info(
                f"{self.identifier}_generation_completed",
                session_id=session_id,
                model=self.get_model_config().model,
            )

            return result

        except Exception as e:
            logger.error(
                f"{self.identifier}_generation_failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            raise
