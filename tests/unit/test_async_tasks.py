"""Tests for async Celery tasks."""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

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


def test_reconcile_session_embeddings_queues_missing_and_stale():
    """Reconcile task should enqueue refresh for missing and stale published embeddings."""
    now = datetime.utcnow()

    settings = Mock(
        enable_embeddings=True,
        embedding_sync_enabled=True,
        embedding_sync_batch_size=100,
        embedding_sync_max_enqueues_per_run=50,
        embedding_sync_stale_threshold_seconds=0,
    )

    # First batch has two published sessions, second batch empty.
    rows = [(1, now), (2, now)]
    published_query = MagicMock()
    published_query.filter.return_value = published_query
    published_query.order_by.return_value = published_query
    published_query.offset.return_value = published_query
    published_query.limit.return_value = published_query
    published_query.all.side_effect = [rows, []]

    existing_ids_query = MagicMock()
    existing_ids_query.filter.return_value = existing_ids_query
    existing_ids_query.all.return_value = [(2,)]

    db_mock = MagicMock()

    def query_side_effect(*args):
        if len(args) == 2:
            return published_query
        return existing_ids_query

    db_mock.query.side_effect = query_side_effect

    embedding_service = Mock()
    # session_1 missing, session_2 stale metadata
    embedding_service.sessions_collection.get.side_effect = [
        {
            "ids": ["session_2"],
            "metadatas": [{"source_updated_at": 0.0}],
        },
        {
            "ids": ["session_2"],
            "metadatas": [],
        },
    ]

    with (
        patch("app.config.settings.get_settings", return_value=settings),
        patch("app.async_jobs.tasks.SessionLocal", return_value=db_mock),
        patch("app.async_jobs.tasks.get_embedding_service", return_value=embedding_service),
        patch.object(tasks_module.generate_session_embedding, "apply_async") as mock_apply_async,
        patch.object(embedding_service.sessions_collection, "delete") as mock_delete,
    ):
        result = tasks_module.reconcile_session_embeddings.run()

    assert result["status"] == "ok"
    assert result["scanned"] == 2
    assert result["missing"] == 1
    assert result["stale"] == 1
    assert result["orphaned"] == 0
    assert result["deleted_orphans"] == 0
    assert result["queued"] == 2
    assert mock_apply_async.call_count == 2
    mock_delete.assert_not_called()
    db_mock.close.assert_called_once()


def test_reconcile_session_embeddings_scopes_to_event_when_provided():
    """Reconcile task should filter published sessions by event_id when provided."""
    now = datetime.utcnow()
    settings = Mock(
        enable_embeddings=True,
        embedding_sync_enabled=True,
        embedding_sync_batch_size=100,
        embedding_sync_max_enqueues_per_run=50,
        embedding_sync_stale_threshold_seconds=0,
    )

    rows = [(11, now)]
    published_query = MagicMock()
    published_query.filter.return_value = published_query
    published_query.order_by.return_value = published_query
    published_query.offset.return_value = published_query
    published_query.limit.return_value = published_query
    published_query.all.side_effect = [rows, []]

    existing_ids_query = MagicMock()
    existing_ids_query.filter.return_value = existing_ids_query
    existing_ids_query.all.return_value = [(11,)]

    db_mock = MagicMock()

    def query_side_effect(*args):
        if len(args) == 2:
            return published_query
        return existing_ids_query

    db_mock.query.side_effect = query_side_effect

    embedding_service = Mock()
    embedding_service.sessions_collection.get.side_effect = [
        {
            "ids": [],
            "metadatas": [],
        },
        {
            "ids": ["session_11", "session_999"],
            "metadatas": [],
        },
    ]

    with (
        patch("app.config.settings.get_settings", return_value=settings),
        patch("app.async_jobs.tasks.SessionLocal", return_value=db_mock),
        patch("app.async_jobs.tasks.get_embedding_service", return_value=embedding_service),
        patch.object(tasks_module.generate_session_embedding, "apply_async") as mock_apply_async,
        patch.object(embedding_service.sessions_collection, "delete") as mock_delete,
    ):
        result = tasks_module.reconcile_session_embeddings.run(event_id=321)

    assert result["status"] == "ok"
    assert result["event_id"] == 321
    assert result["orphaned"] == 1
    assert result["deleted_orphans"] == 1
    assert result["queued"] == 1
    assert mock_apply_async.call_count == 1
    mock_delete.assert_called_once_with(ids=["session_999"])


def test_reconcile_session_embeddings_skips_when_disabled():
    """Reconcile task should no-op when embedding sync is disabled."""
    settings = Mock(
        enable_embeddings=True,
        embedding_sync_enabled=False,
    )

    with patch("app.config.settings.get_settings", return_value=settings):
        result = tasks_module.reconcile_session_embeddings.run()

    assert result["status"] == "skipped"
    assert result["reason"] == "disabled"
