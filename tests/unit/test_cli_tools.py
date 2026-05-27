"""Tests for Typer-based CLI tools."""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from app.cli import run as cli_run
from app.cli.api_keys import app as api_keys_app
from app.cli.main import app as main_cli_app
from app.cli.seed_dev_data import app as seed_dev_data_app
from app.cli.workflow_tasks import app as workflow_tasks_app
from app.database.models import (
    APIKey,
    Base,
    SessionFormat,
    SessionStatus,
    User,
    WorkflowExecution,
    WorkflowExecutionStatus,
)
from app.database.models import Session as SessionModel
from app.security.auth import hash_api_key

runner = CliRunner()


def _build_test_db(tmp_path: Path) -> tuple[str, sessionmaker]:
    """Create a sqlite-backed test database for CLI integration tests."""
    db_path = tmp_path / "cli-test.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    return database_url, sessionmaker(bind=engine)


def _create_session(session_factory: sessionmaker) -> SessionModel:
    """Create a minimal session row for workflow execution tests."""
    db = session_factory()
    user = User(username="workflow-user", email="wf@example.com", type="api", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    now = datetime.utcnow()
    session = SessionModel(
        title="CLI workflow session",
        speakers=[],
        tags=[],
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        status=SessionStatus.DRAFT,
        session_format=SessionFormat.OTHER,
        language="en",
        uri=f"cli-workflow-session-{user.id}",
        owner_id=user.id,
        available_content_identifiers=[],
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    db.close()
    return session


class TestAdminApiKeysCli:
    """Test the Typer admin API key CLI."""

    def test_main_cli_help_shows_short_api_keys_and_completion(self):
        """Top-level help should advertise the short command and completion support."""
        result = runner.invoke(main_cli_app, ["--help"])

        assert result.exit_code == 0
        assert "api-keys" in result.stdout
        assert "--install-completion" in result.stdout

    def test_legacy_api_keys_alias_still_works(self):
        """Legacy alias should remain callable for compatibility."""
        result = runner.invoke(main_cli_app, ["api-keys", "--help"])

        assert result.exit_code == 0
        assert "Manage users" in result.stdout

    def test_users_list_prints_existing_user(self, tmp_path, monkeypatch):
        """List users through the CLI."""
        database_url, session_factory = _build_test_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", database_url)

        db = session_factory()
        db.add(User(username="cli-user", email="cli@example.com", type="api", is_active=True))
        db.commit()
        db.close()

        result = runner.invoke(api_keys_app, ["users", "list"])

        assert result.exit_code == 0
        assert "cli-user" in result.stdout
        assert "cli@example.com" in result.stdout

    def test_keys_create_persists_hashed_key(self, tmp_path, monkeypatch):
        """Create an API key for an existing user via the CLI."""
        database_url, session_factory = _build_test_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", database_url)

        db = session_factory()
        user = User(username="cli-user", email="cli@example.com", type="api", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.close()

        result = runner.invoke(
            api_keys_app,
            ["keys", "create", "--username", "cli-user", "--name", "primary"],
        )

        assert result.exit_code == 0
        assert "API key created." in result.stdout

        api_key_line = next(
            line for line in result.stdout.splitlines() if line.startswith("api_key: ")
        )
        plain_key = api_key_line.split("api_key: ", maxsplit=1)[1]

        db = session_factory()
        created_key = db.query(APIKey).filter(APIKey.user_id == user.id).one()
        assert created_key.name == "primary"
        assert created_key.key_hash == hash_api_key(plain_key)
        db.close()


class TestSeedDevDataCli:
    """Test the Typer seed development data CLI."""

    def test_cli_run_is_exported_from_package(self):
        """Package export should expose the main run entrypoint."""
        assert cli_run is not None

    def test_seed_dev_data_creates_default_user_and_key(self, tmp_path, monkeypatch):
        """Seed the development user and API key through the CLI."""
        database_url, session_factory = _build_test_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", database_url)

        result = runner.invoke(seed_dev_data_app, [])

        assert result.exit_code == 0
        assert "Database seeding completed successfully!" in result.stdout
        assert "api_user" in result.stdout

        db = session_factory()
        user = db.query(User).filter(User.username == "api_user").one()
        api_key = db.query(APIKey).filter(APIKey.user_id == user.id).one()
        assert api_key.key_hash == hash_api_key("test-api-key")
        db.close()


class _FakeInspect:
    """Test double for Celery inspect."""

    def active(self):
        return {"worker-1": []}

    def reserved(self):
        return {"worker-1": []}

    def scheduled(self):
        return {"worker-1": []}


class _PendingAsyncResult:
    """Test double for Celery AsyncResult."""

    state = "PENDING"


class TestWorkflowTasksCli:
    """Test the workflow task recovery CLI."""

    def test_workflow_tasks_list_flags_stale_execution(self, tmp_path, monkeypatch):
        """List command should mark old pending executions as stale."""
        database_url, session_factory = _build_test_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", database_url)

        session = _create_session(session_factory)
        db = session_factory()
        execution = WorkflowExecution(
            session_id=session.id,
            target="summary",
            status=WorkflowExecutionStatus.RUNNING,
            celery_task_id="workflow-1",
            triggered_by="user_triggered",
            created_at=datetime.utcnow() - timedelta(hours=2),
            started_at=datetime.utcnow() - timedelta(hours=2),
        )
        db.add(execution)
        db.commit()
        db.close()

        monkeypatch.setattr(
            "app.cli.workflow_tasks._collect_celery_snapshot",
            lambda: __import__(
                "app.cli.workflow_tasks", fromlist=["CelerySnapshot"]
            ).CelerySnapshot(
                active_ids=set(),
                reserved_ids=set(),
                scheduled_ids=set(),
                inspect_available=True,
                worker_count=1,
            ),
        )
        monkeypatch.setattr(
            "app.cli.workflow_tasks._get_celery_client",
            lambda: type(
                "FakeCeleryClient",
                (),
                {"AsyncResult": staticmethod(lambda _task_id: _PendingAsyncResult())},
            )(),
        )

        result = runner.invoke(
            workflow_tasks_app,
            ["list", "--stale-only", "--older-than-minutes", "30"],
        )

        assert result.exit_code == 0
        assert "stale_executions: 1" in result.stdout
        assert "workflow-1" not in result.stdout
        assert "task is pending beyond threshold" in result.stdout

    def test_workflow_tasks_kill_marks_execution_failed(self, tmp_path, monkeypatch):
        """Kill command should fail the DB record for a stale execution."""
        database_url, session_factory = _build_test_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", database_url)

        session = _create_session(session_factory)
        db = session_factory()
        execution = WorkflowExecution(
            session_id=session.id,
            target="summary",
            status=WorkflowExecutionStatus.RUNNING,
            celery_task_id="workflow-2",
            triggered_by="user_triggered",
            created_at=datetime.utcnow() - timedelta(hours=2),
            started_at=datetime.utcnow() - timedelta(hours=2),
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)
        execution_id = execution.id
        db.close()

        monkeypatch.setattr(
            "app.cli.workflow_tasks._collect_celery_snapshot",
            lambda: __import__(
                "app.cli.workflow_tasks", fromlist=["CelerySnapshot"]
            ).CelerySnapshot(
                active_ids=set(),
                reserved_ids=set(),
                scheduled_ids=set(),
                inspect_available=True,
                worker_count=1,
            ),
        )
        revoke_calls: list[tuple[str, bool, str]] = []
        monkeypatch.setattr(
            "app.cli.workflow_tasks._get_celery_client",
            lambda: type(
                "FakeCeleryClient",
                (),
                {
                    "AsyncResult": staticmethod(lambda _task_id: _PendingAsyncResult()),
                    "control": type(
                        "FakeControl",
                        (),
                        {
                            "revoke": staticmethod(
                                lambda task_id, terminate, signal: revoke_calls.append(
                                    (task_id, terminate, signal)
                                )
                            )
                        },
                    )(),
                },
            )(),
        )

        result = runner.invoke(workflow_tasks_app, ["kill", "--execution-id", str(execution_id)])

        assert result.exit_code == 0
        assert f"Killed workflow execution {execution_id}" in result.stdout
        assert revoke_calls == [("workflow-2", False, "SIGTERM")]

        db = session_factory()
        refreshed = db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).one()
        assert refreshed.status == WorkflowExecutionStatus.FAILED
        assert "Killed via CLI" in refreshed.error
        db.close()

    def test_workflow_tasks_restart_requeues_execution(self, tmp_path, monkeypatch):
        """Restart command should fail the stale record and queue a replacement."""
        database_url, session_factory = _build_test_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", database_url)

        session = _create_session(session_factory)
        db = session_factory()
        execution = WorkflowExecution(
            session_id=session.id,
            target="summary",
            status=WorkflowExecutionStatus.QUEUED,
            celery_task_id="workflow-3",
            triggered_by="user_triggered",
            created_at=datetime.utcnow() - timedelta(hours=2),
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)
        execution_id = execution.id
        db.close()

        monkeypatch.setattr(
            "app.cli.workflow_tasks._collect_celery_snapshot",
            lambda: __import__(
                "app.cli.workflow_tasks", fromlist=["CelerySnapshot"]
            ).CelerySnapshot(
                active_ids=set(),
                reserved_ids=set(),
                scheduled_ids=set(),
                inspect_available=True,
                worker_count=1,
            ),
        )
        monkeypatch.setattr(
            "app.cli.workflow_tasks._get_celery_client",
            lambda: type(
                "FakeCeleryClient",
                (),
                {
                    "AsyncResult": staticmethod(lambda _task_id: _PendingAsyncResult()),
                    "control": type(
                        "FakeControl",
                        (),
                        {"revoke": staticmethod(lambda *_args, **_kwargs: None)},
                    )(),
                },
            )(),
        )

        def fake_create_and_queue(session_id, target, db, triggered_by, created_by_user_id):
            replacement = WorkflowExecution(
                session_id=session_id,
                target=target,
                status=WorkflowExecutionStatus.QUEUED,
                celery_task_id="workflow-99",
                triggered_by=triggered_by,
                created_by_user_id=created_by_user_id,
            )
            db.add(replacement)
            db.commit()
            db.refresh(replacement)
            return replacement, replacement.celery_task_id

        monkeypatch.setattr(
            "app.services.execution_service.WorkflowExecutionService.create_and_queue",
            fake_create_and_queue,
        )

        result = runner.invoke(workflow_tasks_app, ["restart", "--execution-id", str(execution_id)])

        assert result.exit_code == 0
        assert f"Restarted workflow execution {execution_id} as execution" in result.stdout
        assert "workflow-99" in result.stdout

        db = session_factory()
        rows = db.query(WorkflowExecution).order_by(WorkflowExecution.id.asc()).all()
        assert rows[0].status == WorkflowExecutionStatus.FAILED
        assert "Restarted via CLI" in rows[0].error
        assert rows[1].status == WorkflowExecutionStatus.QUEUED
        assert rows[1].celery_task_id == "workflow-99"
        db.close()
