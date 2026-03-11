"""Base class for workflow steps."""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
import structlog

from app.workflows.chat_models import ChatModelConfig, create_chat_model
from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from langchain.chat_models import BaseChatModel

logger = structlog.get_logger()


class WorkflowStep(ABC):
    """
    Base class for workflow steps.
    
    Each step is a self-contained unit of work that:
    1. Generates content using LLM
    2. Persists result to database immediately
    3. Returns result for context chaining to dependent steps
    
    Steps define their own LLM configuration (model, temperature, max_tokens, etc.) for
    task-specific optimization. Use `get_model_config()` to customize the LLM behavior.
    """

    @property
    @abstractmethod
    def identifier(self) -> str:
        """Unique identifier for this step (e.g., 'summary', 'tags')."""
        pass

    @property
    @abstractmethod
    def dependencies(self) -> List[str]:
        """List of step identifiers this step depends on (e.g., ['summary'])."""
        pass

    def get_model_config(self) -> ChatModelConfig:
        """
        Get LLM chat model configuration for this step.
        
        Override this method in subclasses to customize model, temperature, max_tokens, etc.
        for task-specific optimization.
        
        Example:
            def get_model_config(self) -> ChatModelConfig:
                return ChatModelConfig(
                    model="gemma-3-27b-it",
                    temperature=0.7,
                    max_tokens=2000,
                    top_p=0.95,
                )
        
        Returns:
            ChatModelConfig with model settings for this step
        """
        # Default configuration - steps override for optimization
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.7,
            max_tokens=2000,
            top_p=None,
        )
    
    def get_model(self) -> BaseChatModel:
        """
        Helper to create the chat model instance for this step.
        Uses the configuration from `get_model_config()`.

        Returns:
            Initialized LangChain BaseChatModel instance
        """
        config = self.get_model_config()
        return create_chat_model(config)

    async def execute(
        self,
        session_id: int,
        execution_id: int,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute the step: generate content AND persist to database.
        
        Args:
            session_id: ID of the session being processed
            execution_id: ID of the WorkflowExecution record (for tracking)
            context: Shared context dict containing:
                - Results from prior steps
                - Transcription text
        
        Returns:
            Dict with structure:
            {
                "identifier": "content_string"
            }
            This is immediately persisted, return value used for context chaining only.
        
        Raises:
            Exception: If content generation or persistence fails
        """
        from app.database.connection import SessionLocal
        
        db: Optional[Session] = None
        try:
            # Create database session for this step execution
            db = SessionLocal()
            
            logger.info(
                "step_database_session_created",
                step_id=self.identifier,
                session_id=session_id,
                execution_id=execution_id,
                db_type=type(db).__name__,
                db_has_query=hasattr(db, 'query'),
            )
            
            # 1. Generate content
            logger.info(
                "step_generation_starting",
                step_id=self.identifier,
                session_id=session_id,
                execution_id=execution_id,
            )
            
            result = await self._generate(session_id, db, context)
            
            logger.info(
                "step_generation_completed",
                step_id=self.identifier,
                session_id=session_id,
                execution_id=execution_id,
                content_length=len(result.get("content", "")),
            )
            
            # 2. Persist to database
            logger.info(
                "step_persistence_starting",
                step_id=self.identifier,
                session_id=session_id,
                execution_id=execution_id,
            )
            
            self._save_to_db(
                db=db,
                session_id=session_id,
                execution_id=execution_id,
                identifier=self.identifier,
                content=result,
            )
            
            logger.info(
                "step_persistence_completed",
                step_id=self.identifier,
                session_id=session_id,
                execution_id=execution_id,
            )
            
            # 3. Return for context chaining
            return {self.identifier: result.get("content", "")}

        except Exception as e:
            logger.error(
                "step_execution_failed",
                step_id=self.identifier,
                session_id=session_id,
                execution_id=execution_id,
                error=str(e),
                exc_info=True,
            )
            raise
        finally:
            if db:
                db.close()

    @abstractmethod
    async def _generate(
        self, session_id: int, db: Session, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate content using LLM.
        
        Subclasses implement this to define generation logic.
        
        Args:
            session_id: ID of the session being processed
            db: SQLAlchemy database session
            context: Context dict with prior step results + session metadata
        
        Returns:
            Dict with structure:
            {
                "content": "actual content string",
                "content_type": "markdown|json_array|plain_text|...",
                "meta_info": {"model": "gemma-3-27b-it", "type": "generated_summary", ...}
            }
        """
        pass

    def _save_to_db(
        self,
        db: Session,
        session_id: int,
        execution_id: int,
        identifier: str,
        content: Dict[str, Any],
    ) -> None:
        """
        Save generated content to database.
        
        Uses create_or_update to handle retries gracefully - if a workflow
        is retried and this step runs again, it will update the existing
        content instead of trying to insert a duplicate.
        
        Args:
            db: SQLAlchemy database session
            session_id: Session ID
            execution_id: WorkflowExecution ID for tracking
            identifier: Step identifier (content key)
            content: Dict with "content", "content_type", "meta_info"
        """
        db_content = content_crud.create_or_update_content(
            db=db,
            session_id=session_id,
            identifier=identifier,
            content=content.get("content", ""),
            content_type=content.get("content_type", "plain_text"),
            workflow_execution_id=execution_id,
            meta_info=content.get("meta_info"),
        )
        
        # Add to session's available content identifiers
        session_crud.add_available_content_identifier(db, session_id, identifier)
        
        logger.info(
            "content_saved_to_db",
            step_id=identifier,
            session_id=session_id,
            execution_id=execution_id,
            content_id=db_content.id,
        )
