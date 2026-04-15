"""Service layer for workflow execution lifecycle management."""

from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.crud import generated_content as content_crud
from app.crud.session import session_crud
from app.database.models import WorkflowExecution
from app.workflows.execution_context import is_workflow_target

logger = structlog.get_logger()


class WorkflowExecutionService:
    """
    Service layer for managing workflow execution lifecycle.

    Responsibilities:
    - Validate that prerequisites are met (session exists, transcription exists)
    - Resolve target (workflow name or step identifier) to list of steps
    - Create WorkflowExecution records for tracking
    - Queue Celery tasks for async execution
    - Update execution status throughout lifecycle
    - Save results to database

    This layer separates HTTP/route logic from business logic.
    """

    @staticmethod
    def _get_first_stage_steps(target: str) -> list[str]:
        """
        Get step identifiers that execute first in a target.

        For a single step target, returns that step.
        For a workflow, finds all steps with no context_requirements (can start independently).

        Args:
            target: Workflow name or step identifier

        Returns:
            List of step identifiers that are first-stage
        """
        from app.workflows.execution_context import is_workflow_target

        # If it's a single step, that's the first-stage step
        if not is_workflow_target(target):
            return [target]

        # For workflows, find all steps with no context requirements
        # (i.e., steps that don't depend on output from other steps to start)
        from app.workflows.execution_context import StepRegistry as SR

        first_stage = []
        for step_id, context_requirements in SR._step_context_requirements.items():
            if not context_requirements:  # No context requirements = first-stage step
                first_stage.append(step_id)

        return first_stage

    @staticmethod
    def validate_and_prepare(
        session_id: int,
        target: str,
        db: Session,
    ) -> tuple[bool, str]:
        """
        Validate prerequisites and determine execution type.

        Args:
            session_id: Session ID to generate content for
            target: Either a workflow name ("talk_workflow") or step identifier ("summary")
            db: Database session

        Returns:
            Tuple of (is_workflow, execution_type_label) where:
            - is_workflow: True if target is a workflow, False if it's a step
            - execution_type_label: "workflow" or "step" (for logging)

        Raises:
            ValueError: If validation fails
        """
        # Validate session exists
        db_session = session_crud.read(db, session_id)
        if not db_session:
            raise ValueError(f"Session {session_id} not found")

        logger.info(
            "workflow_validation_session_found",
            session_id=session_id,
            target=target,
        )

        # Determine if target is workflow or step
        try:
            is_workflow = is_workflow_target(target)
        except ValueError as e:
            logger.error(
                "workflow_target_resolution_failed",
                session_id=session_id,
                target=target,
                error=str(e),
            )
            raise ValueError(f"Unknown target: '{target}'") from e

        # Determine execution type for logging
        execution_type = "workflow" if is_workflow else "step"

        logger.info(
            "workflow_validation_complete",
            session_id=session_id,
            target=target,
            execution_type=execution_type,
            is_workflow=is_workflow,
        )

        return is_workflow, execution_type

    @staticmethod
    def create_and_queue(
        session_id: int,
        target: str,
        db: Session,
        triggered_by: str = "user_triggered",
        created_by_user_id: int | None = None,
    ) -> tuple[WorkflowExecution, str]:
        """
        Create execution record and queue Celery task.

        This is the main entry point for triggering an execution.

        Args:
            session_id: Session to generate content for
            target: Workflow name or step identifier
            db: Database session
            triggered_by: "user_triggered" or "auto_scheduled"
            created_by_user_id: User who triggered (optional)

        Returns:
            Tuple of (WorkflowExecution record, celery_task_id)

        Raises:
            ValueError: If validation fails
        """
        # Validate and resolve (still need to validate prerequisites)
        is_workflow, execution_type = WorkflowExecutionService.validate_and_prepare(
            session_id, target, db
        )

        logger.info(
            "workflow_create_and_queue_starting",
            session_id=session_id,
            target=target,
            execution_type=execution_type,
            is_workflow=is_workflow,
        )

        # Validate scheduling requirements for first-stage steps
        first_stage_steps = WorkflowExecutionService._get_first_stage_steps(target)
        logger.info(
            "validating_first_stage_step_requirements",
            session_id=session_id,
            target=target,
            first_stage_steps=first_stage_steps,
        )

        from app.workflows.execution_context import StepRegistry

        for step_id in first_stage_steps:
            try:
                step = StepRegistry.get_step(step_id)
                step.validate_scheduling_requirements(session_id, db)
                logger.info(
                    "first_stage_step_scheduling_validated",
                    session_id=session_id,
                    step_id=step_id,
                )
            except ValueError as e:
                logger.warning(
                    "first_stage_step_scheduling_validation_failed",
                    session_id=session_id,
                    step_id=step_id,
                    error=str(e),
                )
                raise

        # Create execution record
        workflow_exec = content_crud.create_workflow_execution(
            db=db,
            session_id=session_id,
            target=target,  # Store the original target
            triggered_by=triggered_by,
            created_by_user_id=created_by_user_id,
        )

        logger.info(
            "workflow_execution_record_created",
            session_id=session_id,
            execution_id=workflow_exec.id,
            target=target,
            status=workflow_exec.status,
        )

        # Generate unique task ID
        celery_task_id = f"workflow-{workflow_exec.id}"

        # Lazy import to avoid circular imports
        from app.async_jobs.tasks import execute_generated_content

        # Queue Celery task with target (not step_ids)
        task = execute_generated_content.apply_async(
            (
                session_id,
                workflow_exec.id,
                target,  # Pass target instead of step_ids
                triggered_by,
                created_by_user_id,
            ),
            task_id=celery_task_id,
        )

        logger.info(
            "workflow_celery_task_queued",
            session_id=session_id,
            execution_id=workflow_exec.id,
            celery_task_id=celery_task_id,
            celery_task_state=task.state,
            is_workflow=is_workflow,
        )

        # Update execution record with task ID
        workflow_exec.celery_task_id = celery_task_id
        db.commit()

        logger.info(
            "workflow_execution_queued",
            session_id=session_id,
            execution_id=workflow_exec.id,
            celery_task_id=celery_task_id,
            target=target,
            execution_type=execution_type,
            is_workflow=is_workflow,
        )

        return workflow_exec, celery_task_id

    @staticmethod
    def get_execution_status(execution_id: int, db: Session) -> WorkflowExecution | None:
        """
        Get current status of a workflow execution.

        Args:
            execution_id: WorkflowExecution ID
            db: Database session

        Returns:
            WorkflowExecution record or None if not found
        """
        return content_crud.get_workflow_execution(db, execution_id)

    @staticmethod
    def get_execution_by_celery_task_id(task_id: str, db: Session) -> WorkflowExecution | None:
        """
        Get workflow execution by Celery task ID.

        Args:
            task_id: Celery task ID
            db: Database session

        Returns:
            WorkflowExecution record or None if not found
        """
        return content_crud.get_workflow_execution_by_task_id(db, task_id)

    @staticmethod
    @staticmethod
    def mark_running(
        execution_id: int,
        db: Session,
        celery_task_id: str | None = None,
    ) -> None:
        """
        Mark execution as running.

        Called when Celery task starts.

        Args:
            execution_id: WorkflowExecution ID
            db: Database session
            celery_task_id: Optional Celery task ID assigned by broker
        """
        from app.database.models import WorkflowExecutionStatus

        workflow_exec = content_crud.get_workflow_execution(db, execution_id)
        if not workflow_exec:
            logger.error(
                "workflow_execution_not_found_for_running",
                execution_id=execution_id,
            )
            return

        if celery_task_id:
            workflow_exec.celery_task_id = celery_task_id
        workflow_exec.status = WorkflowExecutionStatus.RUNNING
        workflow_exec.started_at = datetime.utcnow()
        db.commit()

        logger.info(
            "workflow_execution_marked_running",
            execution_id=execution_id,
        )

    @staticmethod
    def mark_completed(
        execution_id: int,
        db: Session,
        created_content_ids: list[int] | None = None,
    ) -> None:
        """
        Mark execution as completed.

        Called when all steps successfully complete.

        Args:
            execution_id: WorkflowExecution ID
            db: Database session
            created_content_ids: List of generated content IDs (for logging)
        """
        from app.database.models import WorkflowExecutionStatus

        workflow_exec = content_crud.get_workflow_execution(db, execution_id)
        if not workflow_exec:
            logger.error(
                "workflow_execution_not_found_for_completion",
                execution_id=execution_id,
            )
            return

        workflow_exec.status = WorkflowExecutionStatus.COMPLETED
        workflow_exec.completed_at = datetime.utcnow()
        db.commit()

        logger.info(
            "workflow_execution_marked_completed",
            execution_id=execution_id,
            created_content_ids=created_content_ids or [],
            duration_seconds=(
                workflow_exec.completed_at - workflow_exec.created_at
            ).total_seconds(),
        )

    @staticmethod
    def mark_failed(
        execution_id: int,
        db: Session,
        error: str,
    ) -> None:
        """
        Mark execution as failed.

        Called when any step fails.

        Args:
            execution_id: WorkflowExecution ID
            db: Database session
            error: Error message explaining the failure
        """
        from app.database.models import WorkflowExecutionStatus

        workflow_exec = content_crud.get_workflow_execution(db, execution_id)
        if not workflow_exec:
            logger.error(
                "workflow_execution_not_found_for_failure",
                execution_id=execution_id,
            )
            return

        workflow_exec.status = WorkflowExecutionStatus.FAILED
        workflow_exec.error = error
        workflow_exec.completed_at = datetime.utcnow()
        db.commit()

        logger.info(
            "workflow_execution_marked_failed",
            execution_id=execution_id,
            error=error,
        )
