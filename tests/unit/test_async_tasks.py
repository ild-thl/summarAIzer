"""Tests for async Celery tasks."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.async_jobs import tasks as tasks_module
from app.database.models import WorkflowExecutionStatus


@pytest.mark.asyncio
async def test_execute_generated_content_stores_created_by_user_id(
    mock_db_session, clean_registries
):
    """Test that execute_generated_content stores created_by_user_id in WorkflowExecution."""
    # Setup mock Celery task context
    mock_celery_task = Mock()
    mock_celery_task.request = Mock(id="task-123", retries=0)
    mock_celery_task.max_retries = 2

    # Setup mock created WorkflowExecution
    mock_workflow_exec = Mock()
    mock_workflow_exec.id = 1
    mock_workflow_exec.created_by_user_id = None
    mock_workflow_exec.status = WorkflowExecutionStatus.RUNNING
    mock_workflow_exec.created_at = datetime.utcnow()

    # Setup mock transcription content
    mock_transcription = Mock()
    mock_transcription.id = 1
    mock_transcription.content = "Sample transcription text"

    # Setup mocks for graph execution
    mock_graph = Mock()
    mock_final_state = {
        "session_id": 1,
        "execution_id": 1,
        "transcription": "Sample transcription text",
        "summary": {"content": "Summary content"},
    }

    # Setup database query mocks
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_workflow_exec)))
        )
    )

    # Setup mock CRUD operations
    with (
        patch("app.async_jobs.tasks.content_crud") as mock_content_crud,
        patch("app.async_jobs.tasks.SessionLocal") as mock_session_local,
        patch("app.async_jobs.tasks._resolve_and_build_workflow") as mock_resolve,
        patch("app.async_jobs.tasks._execute_workflow_graph") as mock_execute,
        patch("app.async_jobs.tasks._track_generated_content") as mock_track,
    ):

        # Configure mocks
        mock_session_local.return_value = mock_db_session
        mock_content_crud.get_content_by_identifier.return_value = mock_transcription
        mock_content_crud.get_workflow_execution.return_value = mock_workflow_exec
        mock_resolve.return_value = mock_graph
        mock_execute.return_value = mock_final_state
        mock_track.return_value = [1, 2, 3]  # List of created content IDs

        # Execute task with created_by_user_id using run() method
        created_by_user_id = 42
        result = tasks_module.execute_generated_content.run(
            session_id=1,
            execution_id=1,
            target="talk_workflow",
            triggered_by="user_triggered",
            created_by_user_id=created_by_user_id,
        )

        # Verify created_by_user_id was set on the WorkflowExecution
        assert mock_workflow_exec.created_by_user_id == created_by_user_id
        mock_db_session.commit.assert_called()

        # Verify result
        assert result == {
            "status": "completed",
            "execution_id": 1,
            "created_ids": [1, 2, 3],
        }


@pytest.mark.asyncio
async def test_execute_generated_content_without_created_by_user_id(
    mock_db_session, clean_registries
):
    """Test that execute_generated_content works without created_by_user_id."""
    # Setup mock created WorkflowExecution
    mock_workflow_exec = Mock()
    mock_workflow_exec.id = 1
    mock_workflow_exec.created_by_user_id = None
    mock_workflow_exec.status = WorkflowExecutionStatus.RUNNING
    mock_workflow_exec.created_at = datetime.utcnow()

    # Setup mock transcription content
    mock_transcription = Mock()
    mock_transcription.id = 1
    mock_transcription.content = "Sample transcription text"

    # Setup mocks for graph execution
    mock_graph = Mock()
    mock_final_state = {
        "session_id": 1,
        "execution_id": 1,
        "transcription": "Sample transcription text",
        "summary": {"content": "Summary content"},
    }

    # Setup database query mocks
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_workflow_exec)))
        )
    )

    # Setup mock CRUD operations
    with (
        patch("app.async_jobs.tasks.content_crud") as mock_content_crud,
        patch("app.async_jobs.tasks.SessionLocal") as mock_session_local,
        patch("app.async_jobs.tasks._resolve_and_build_workflow") as mock_resolve,
        patch("app.async_jobs.tasks._execute_workflow_graph") as mock_execute,
        patch("app.async_jobs.tasks._track_generated_content") as mock_track,
    ):

        # Configure mocks
        mock_session_local.return_value = mock_db_session
        mock_content_crud.get_content_by_identifier.return_value = mock_transcription
        mock_content_crud.get_workflow_execution.return_value = mock_workflow_exec
        mock_resolve.return_value = mock_graph
        mock_execute.return_value = mock_final_state
        mock_track.return_value = [1, 2, 3]

        # Execute task without created_by_user_id (None)
        result = tasks_module.execute_generated_content.run(
            session_id=1,
            execution_id=1,
            target="talk_workflow",
            triggered_by="user_triggered",
            created_by_user_id=None,
        )

        # Verify created_by_user_id was NOT changed (remains None)
        assert mock_workflow_exec.created_by_user_id is None

        # Verify result
        assert result == {
            "status": "completed",
            "execution_id": 1,
            "created_ids": [1, 2, 3],
        }


@pytest.mark.asyncio
async def test_execute_generated_content_logs_user_context(mock_db_session, clean_registries):
    """Test that execute_generated_content logs when storing user context."""
    # Setup mock created WorkflowExecution
    mock_workflow_exec = Mock()
    mock_workflow_exec.id = 1
    mock_workflow_exec.created_by_user_id = None
    mock_workflow_exec.status = WorkflowExecutionStatus.RUNNING
    mock_workflow_exec.created_at = datetime.utcnow()

    # Setup mock transcription content
    mock_transcription = Mock()
    mock_transcription.id = 1
    mock_transcription.content = "Sample transcription text"

    # Setup mocks for graph execution
    mock_graph = Mock()
    mock_final_state = {
        "session_id": 1,
        "execution_id": 1,
        "transcription": "Sample transcription text",
        "summary": {"content": "Summary content"},
    }

    # Setup database query mocks
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_workflow_exec)))
        )
    )

    # Setup mock CRUD operations
    with (
        patch("app.async_jobs.tasks.content_crud") as mock_content_crud,
        patch("app.async_jobs.tasks.SessionLocal") as mock_session_local,
        patch("app.async_jobs.tasks._resolve_and_build_workflow") as mock_resolve,
        patch("app.async_jobs.tasks._execute_workflow_graph") as mock_execute,
        patch("app.async_jobs.tasks._track_generated_content") as mock_track,
        patch("app.async_jobs.tasks.logger") as mock_logger,
    ):

        # Configure mocks
        mock_session_local.return_value = mock_db_session
        mock_content_crud.get_content_by_identifier.return_value = mock_transcription
        mock_content_crud.get_workflow_execution.return_value = mock_workflow_exec
        mock_resolve.return_value = mock_graph
        mock_execute.return_value = mock_final_state
        mock_track.return_value = [1, 2, 3]

        # Execute task
        created_by_user_id = 99
        tasks_module.execute_generated_content.run(
            session_id=1,
            execution_id=1,
            target="talk_workflow",
            created_by_user_id=created_by_user_id,
        )

        # Verify logging of user context storage
        log_calls = mock_logger.info.call_args_list
        user_context_logged = any(
            call[0] == ("workflow_execution_user_context_stored",)
            and call[1].get("created_by_user_id") == created_by_user_id
            for call in log_calls
        )
        assert user_context_logged, "Should log workflow_execution_user_context_stored"


@pytest.mark.asyncio
async def test_execute_generated_content_database_session_closed(mock_db_session, clean_registries):
    """Test that execute_generated_content properly closes database session."""
    # Setup mock created WorkflowExecution
    mock_workflow_exec = Mock()
    mock_workflow_exec.id = 1
    mock_workflow_exec.created_by_user_id = None
    mock_workflow_exec.status = WorkflowExecutionStatus.RUNNING
    mock_workflow_exec.created_at = datetime.utcnow()

    # Setup mock transcription content
    mock_transcription = Mock()
    mock_transcription.id = 1
    mock_transcription.content = "Sample transcription text"

    # Setup mocks for graph execution
    mock_graph = Mock()
    mock_final_state = {
        "session_id": 1,
        "execution_id": 1,
        "transcription": "Sample transcription text",
    }

    # Setup database query mocks
    mock_db_session.query = Mock(
        return_value=Mock(
            filter=Mock(return_value=Mock(first=Mock(return_value=mock_workflow_exec)))
        )
    )

    # Setup mock CRUD operations
    with (
        patch("app.async_jobs.tasks.content_crud") as mock_content_crud,
        patch("app.async_jobs.tasks.SessionLocal") as mock_session_local,
        patch("app.async_jobs.tasks._resolve_and_build_workflow") as mock_resolve,
        patch("app.async_jobs.tasks._execute_workflow_graph") as mock_execute,
        patch("app.async_jobs.tasks._track_generated_content") as mock_track,
    ):

        # Configure mocks
        mock_session_local.return_value = mock_db_session
        mock_content_crud.get_content_by_identifier.return_value = mock_transcription
        mock_content_crud.get_workflow_execution.return_value = mock_workflow_exec
        mock_resolve.return_value = mock_graph
        mock_execute.return_value = mock_final_state
        mock_track.return_value = []

        # Execute task
        tasks_module.execute_generated_content.run(
            session_id=1,
            execution_id=1,
            target="talk_workflow",
            created_by_user_id=42,
        )

        # Verify database session was closed
        mock_db_session.close.assert_called_once()
