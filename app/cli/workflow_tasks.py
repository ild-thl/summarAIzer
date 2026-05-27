"""CLI tooling for observing and recovering workflow executions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any

import typer
from celery import Celery
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.cli.common import db_session, load_environment
from app.database.models import WorkflowExecution, WorkflowExecutionStatus

load_environment()

app = typer.Typer(
    help="Inspect running workflow executions and repair stale Celery-backed tasks.",
    no_args_is_help=True,
)

_ACTIVE_DB_STATUSES = {WorkflowExecutionStatus.QUEUED.value, WorkflowExecutionStatus.RUNNING.value}
_TERMINAL_CELERY_STATES = {"SUCCESS", "FAILURE", "REVOKED"}


@lru_cache(maxsize=1)
def _get_celery_client() -> Celery:
    """Create a lightweight Celery client without importing worker task modules."""
    import os

    return Celery(
        "summaraizer_cli",
        broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    )


@dataclass
class CelerySnapshot:
    """Current worker-visible task membership snapshot."""

    active_ids: set[str]
    reserved_ids: set[str]
    scheduled_ids: set[str]
    inspect_available: bool
    worker_count: int
    error: str | None = None


@dataclass
class ExecutionObservation:
    """Joined view of DB execution metadata and current Celery state."""

    execution: WorkflowExecution
    db_status: str
    celery_state: str
    age_minutes: int
    stale_reason: str | None

    @property
    def is_stale(self) -> bool:
        """Whether the execution should be treated as stale."""
        return self.stale_reason is not None


def _status_value(status: Any) -> str:
    """Normalize enum-like status objects to plain strings."""
    return status.value if hasattr(status, "value") else str(status)


def _collect_ids(task_entries: Any) -> set[str]:
    """Extract Celery task ids from inspect payloads."""
    if not isinstance(task_entries, dict):
        return set()

    task_ids: set[str] = set()
    for entries in task_entries.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            task_id = entry.get("id")
            if isinstance(task_id, str) and task_id:
                task_ids.add(task_id)
                continue
            request = entry.get("request")
            if isinstance(request, dict):
                request_id = request.get("id")
                if isinstance(request_id, str) and request_id:
                    task_ids.add(request_id)
    return task_ids


def _collect_celery_snapshot() -> CelerySnapshot:
    """Collect active, reserved, and scheduled task ids from Celery workers."""
    try:
        celery_client = _get_celery_client()
        inspect = celery_client.control.inspect(timeout=1.0)
        active = inspect.active() if inspect else None
        reserved = inspect.reserved() if inspect else None
        scheduled = inspect.scheduled() if inspect else None
        worker_names = set()
        for payload in (active, reserved, scheduled):
            if isinstance(payload, dict):
                worker_names.update(payload.keys())
        return CelerySnapshot(
            active_ids=_collect_ids(active),
            reserved_ids=_collect_ids(reserved),
            scheduled_ids=_collect_ids(scheduled),
            inspect_available=bool(worker_names),
            worker_count=len(worker_names),
        )
    except Exception as exc:
        return CelerySnapshot(
            active_ids=set(),
            reserved_ids=set(),
            scheduled_ids=set(),
            inspect_available=False,
            worker_count=0,
            error=str(exc),
        )


def _lookup_celery_state(task_id: str | None, snapshot: CelerySnapshot) -> str:
    """Resolve the effective current Celery state for a task id."""
    if not task_id:
        return "MISSING"
    if task_id in snapshot.active_ids:
        return "ACTIVE"
    if task_id in snapshot.reserved_ids:
        return "RESERVED"
    if task_id in snapshot.scheduled_ids:
        return "SCHEDULED"

    try:
        result = _get_celery_client().AsyncResult(task_id)
        state = getattr(result, "state", None)
        return str(state).upper() if state else "UNKNOWN"
    except Exception as exc:
        return f"ERROR:{exc}"


def _execution_age_minutes(execution: WorkflowExecution) -> int:
    """Return age in whole minutes based on started_at or created_at."""
    reference_time = execution.started_at or execution.created_at or datetime.utcnow()
    return max(0, int((datetime.utcnow() - reference_time).total_seconds() // 60))


def _detect_stale_reason(
    execution: WorkflowExecution,
    db_status: str,
    celery_state: str,
    snapshot: CelerySnapshot,
    older_than_minutes: int,
) -> str | None:
    """Determine whether an active DB execution record is stale."""
    if db_status not in _ACTIVE_DB_STATUSES:
        return None

    age_minutes = _execution_age_minutes(execution)
    if not execution.celery_task_id:
        return "missing celery task id"

    if celery_state in _TERMINAL_CELERY_STATES:
        return f"database says {db_status} but celery is {celery_state.lower()}"

    if celery_state in {"ACTIVE", "RESERVED", "SCHEDULED"}:
        return None

    return _detect_non_live_stale_reason(
        celery_state=celery_state,
        snapshot=snapshot,
        age_minutes=age_minutes,
        older_than_minutes=older_than_minutes,
    )


def _detect_non_live_stale_reason(
    celery_state: str,
    snapshot: CelerySnapshot,
    age_minutes: int,
    older_than_minutes: int,
) -> str | None:
    """Detect stale conditions for non-live Celery states."""
    if celery_state in {"STARTED", "RETRY"}:
        return _stale_started_retry_reason(celery_state, age_minutes, older_than_minutes)

    if celery_state == "PENDING":
        return _stale_pending_reason(snapshot.inspect_available, age_minutes, older_than_minutes)

    if celery_state == "MISSING":
        return "missing celery task id"

    if age_minutes < older_than_minutes:
        return None

    if celery_state.startswith("ERROR:"):
        return celery_state

    if celery_state == "UNKNOWN":
        return "celery state unknown beyond threshold"

    return None


def _stale_started_retry_reason(
    celery_state: str,
    age_minutes: int,
    older_than_minutes: int,
) -> str | None:
    """Return stale reason when backend reports STARTED/RETRY without ownership."""
    if age_minutes < older_than_minutes:
        return None
    return f"celery reports {celery_state.lower()} but no worker currently owns the task"


def _stale_pending_reason(
    inspect_available: bool,
    age_minutes: int,
    older_than_minutes: int,
) -> str | None:
    """Return stale reason for lingering pending tasks."""
    if age_minutes < older_than_minutes:
        return None
    if inspect_available:
        return "task is pending beyond threshold and not visible on any worker"
    return "task is pending beyond threshold while celery inspect is unavailable"


def _observe_execution(
    execution: WorkflowExecution,
    snapshot: CelerySnapshot,
    older_than_minutes: int,
) -> ExecutionObservation:
    """Build one reconciled execution observation."""
    db_status = _status_value(execution.status)
    celery_state = _lookup_celery_state(execution.celery_task_id, snapshot)
    stale_reason = _detect_stale_reason(
        execution,
        db_status,
        celery_state,
        snapshot,
        older_than_minutes,
    )
    return ExecutionObservation(
        execution=execution,
        db_status=db_status,
        celery_state=celery_state,
        age_minutes=_execution_age_minutes(execution),
        stale_reason=stale_reason,
    )


def _list_candidate_executions(
    db: Session,
    limit: int,
    session_id: int | None,
) -> list[WorkflowExecution]:
    """List active workflow executions ordered from newest to oldest."""
    query = (
        db.query(WorkflowExecution)
        .filter(
            WorkflowExecution.status.in_(
                [WorkflowExecutionStatus.QUEUED, WorkflowExecutionStatus.RUNNING]
            )
        )
        .order_by(desc(WorkflowExecution.created_at))
    )
    if session_id is not None:
        query = query.filter(WorkflowExecution.session_id == session_id)

    return query.limit(limit).all()


def _get_execution(db: Session, execution_id: int) -> WorkflowExecution:
    """Resolve one workflow execution or raise a user-facing error."""
    execution = db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()
    if execution is None:
        raise LookupError(f"Workflow execution {execution_id} not found")
    return execution


def _mark_failed(db: Session, execution_id: int, reason: str) -> None:
    """Mark an execution as failed through the service layer."""
    from app.services.execution_service import WorkflowExecutionService

    WorkflowExecutionService.mark_failed(execution_id, db, reason)


def _revoke_task(task_id: str | None, terminate: bool, signal: str) -> None:
    """Best-effort Celery task revoke."""
    if not task_id:
        return
    _get_celery_client().control.revoke(task_id, terminate=terminate, signal=signal)


def _restart_execution(
    db: Session,
    observation: ExecutionObservation,
    terminate: bool,
    signal: str,
) -> tuple[WorkflowExecution, str]:
    """Mark one stale execution failed and queue a replacement."""
    from app.services.execution_service import WorkflowExecutionService

    _revoke_task(observation.execution.celery_task_id, terminate=terminate, signal=signal)
    _mark_failed(
        db,
        observation.execution.id,
        (
            "Restarted via CLI after stale detection; "
            f"previous_status={observation.db_status}; celery_state={observation.celery_state}; "
            f"reason={observation.stale_reason or 'manual restart'}"
        ),
    )
    return WorkflowExecutionService.create_and_queue(
        session_id=observation.execution.session_id,
        target=observation.execution.target,
        db=db,
        triggered_by=observation.execution.triggered_by,
        created_by_user_id=observation.execution.created_by_user_id,
    )


def _kill_execution(
    db: Session,
    observation: ExecutionObservation,
    terminate: bool,
    signal: str,
) -> None:
    """Revoke a Celery task if present and mark the execution failed."""
    _revoke_task(observation.execution.celery_task_id, terminate=terminate, signal=signal)
    _mark_failed(
        db,
        observation.execution.id,
        (
            "Killed via CLI; "
            f"previous_status={observation.db_status}; celery_state={observation.celery_state}; "
            f"reason={observation.stale_reason or 'manual kill'}"
        ),
    )


@app.command("list")
def list_workflow_tasks(
    older_than_minutes: Annotated[
        int,
        typer.Option("--older-than-minutes", min=1, help="Stale threshold in minutes"),
    ] = 30,
    stale_only: Annotated[
        bool,
        typer.Option("--stale-only", help="Show only stale executions"),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=500, help="Maximum executions to inspect"),
    ] = 100,
    session_id: Annotated[
        int | None,
        typer.Option("--session-id", help="Filter by session id"),
    ] = None,
) -> None:
    """List active workflow executions and flag stale records."""
    with db_session() as db:
        snapshot = _collect_celery_snapshot()
        observations = [
            _observe_execution(execution, snapshot, older_than_minutes)
            for execution in _list_candidate_executions(db, limit, session_id)
        ]
        if stale_only:
            observations = [observation for observation in observations if observation.is_stale]

        typer.echo(
            "inspect_available: "
            f"{str(snapshot.inspect_available).lower()}\tworkers: {snapshot.worker_count}\t"
            f"inspect_error: {snapshot.error or '-'}"
        )

        if not observations:
            typer.echo("No matching workflow executions found.")
            return

        stale_count = sum(1 for observation in observations if observation.is_stale)
        typer.echo(
            f"matching_executions: {len(observations)}\tstale_executions: {stale_count}\t"
            f"threshold_minutes: {older_than_minutes}"
        )
        typer.echo(
            "execution_id\tsession_id\ttarget\tdb_status\tcelery_state\tage_min\tstale\treason"
        )
        for observation in observations:
            typer.echo(
                f"{observation.execution.id}\t{observation.execution.session_id}\t"
                f"{observation.execution.target}\t{observation.db_status}\t"
                f"{observation.celery_state}\t{observation.age_minutes}\t"
                f"{str(observation.is_stale).lower()}\t{observation.stale_reason or '-'}"
            )


@app.command("kill")
def kill_workflow_task(
    execution_id: Annotated[
        int,
        typer.Option("--execution-id", help="Workflow execution id to kill"),
    ],
    older_than_minutes: Annotated[
        int,
        typer.Option("--older-than-minutes", min=1, help="Stale threshold in minutes"),
    ] = 30,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow killing even if the execution is not stale"),
    ] = False,
    terminate: Annotated[
        bool,
        typer.Option("--terminate", help="Send terminate to Celery when revoking"),
    ] = False,
    signal: Annotated[
        str,
        typer.Option("--signal", help="Signal to use when --terminate is set"),
    ] = "SIGTERM",
) -> None:
    """Mark one execution failed and revoke its Celery task if present."""
    with db_session() as db:
        snapshot = _collect_celery_snapshot()
        observation = _observe_execution(
            _get_execution(db, execution_id), snapshot, older_than_minutes
        )
        if not observation.is_stale and not force:
            typer.echo(
                "Refusing to kill a non-stale execution without --force. "
                f"Current celery state: {observation.celery_state}",
                err=True,
            )
            raise typer.Exit(code=1)

        _kill_execution(db, observation, terminate=terminate, signal=signal)
        typer.echo(
            f"Killed workflow execution {observation.execution.id}. "
            f"db_status={observation.db_status} celery_state={observation.celery_state}"
        )


@app.command("restart")
def restart_workflow_task(
    execution_id: Annotated[
        int,
        typer.Option("--execution-id", help="Workflow execution id to restart"),
    ],
    older_than_minutes: Annotated[
        int,
        typer.Option("--older-than-minutes", min=1, help="Stale threshold in minutes"),
    ] = 30,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow restarting even if the execution is not stale"),
    ] = False,
    terminate: Annotated[
        bool,
        typer.Option("--terminate", help="Send terminate to Celery when revoking"),
    ] = False,
    signal: Annotated[
        str,
        typer.Option("--signal", help="Signal to use when --terminate is set"),
    ] = "SIGTERM",
) -> None:
    """Mark one stale execution failed and queue a replacement execution."""
    with db_session() as db:
        snapshot = _collect_celery_snapshot()
        observation = _observe_execution(
            _get_execution(db, execution_id), snapshot, older_than_minutes
        )
        if not observation.is_stale and not force:
            typer.echo(
                "Refusing to restart a non-stale execution without --force. "
                f"Current celery state: {observation.celery_state}",
                err=True,
            )
            raise typer.Exit(code=1)

        replacement_execution, replacement_task_id = _restart_execution(
            db,
            observation,
            terminate=terminate,
            signal=signal,
        )
        typer.echo(
            f"Restarted workflow execution {observation.execution.id} as "
            f"execution {replacement_execution.id} with task {replacement_task_id}."
        )


@app.command("kill-stale")
def kill_stale_workflow_tasks(
    older_than_minutes: Annotated[
        int,
        typer.Option("--older-than-minutes", min=1, help="Stale threshold in minutes"),
    ] = 30,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=500, help="Maximum executions to inspect"),
    ] = 100,
    session_id: Annotated[
        int | None,
        typer.Option("--session-id", help="Filter by session id"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Apply the kill instead of only previewing matches"),
    ] = False,
    terminate: Annotated[
        bool,
        typer.Option("--terminate", help="Send terminate to Celery when revoking"),
    ] = False,
    signal: Annotated[
        str,
        typer.Option("--signal", help="Signal to use when --terminate is set"),
    ] = "SIGTERM",
) -> None:
    """Kill all stale workflow executions matching the filter."""
    with db_session() as db:
        snapshot = _collect_celery_snapshot()
        stale_observations = [
            observation
            for observation in (
                _observe_execution(execution, snapshot, older_than_minutes)
                for execution in _list_candidate_executions(db, limit, session_id)
            )
            if observation.is_stale
        ]

        if not stale_observations:
            typer.echo("No stale workflow executions found.")
            return

        if not yes:
            typer.echo(
                f"Found {len(stale_observations)} stale executions. Re-run with --yes to kill them."
            )
            return

        for observation in stale_observations:
            _kill_execution(db, observation, terminate=terminate, signal=signal)

        typer.echo(f"Killed {len(stale_observations)} stale workflow executions.")


@app.command("restart-stale")
def restart_stale_workflow_tasks(
    older_than_minutes: Annotated[
        int,
        typer.Option("--older-than-minutes", min=1, help="Stale threshold in minutes"),
    ] = 30,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=500, help="Maximum executions to inspect"),
    ] = 100,
    session_id: Annotated[
        int | None,
        typer.Option("--session-id", help="Filter by session id"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Apply the restart instead of only previewing matches"),
    ] = False,
    terminate: Annotated[
        bool,
        typer.Option("--terminate", help="Send terminate to Celery when revoking"),
    ] = False,
    signal: Annotated[
        str,
        typer.Option("--signal", help="Signal to use when --terminate is set"),
    ] = "SIGTERM",
) -> None:
    """Restart all stale workflow executions matching the filter."""
    with db_session() as db:
        snapshot = _collect_celery_snapshot()
        stale_observations = [
            observation
            for observation in (
                _observe_execution(execution, snapshot, older_than_minutes)
                for execution in _list_candidate_executions(db, limit, session_id)
            )
            if observation.is_stale
        ]

        if not stale_observations:
            typer.echo("No stale workflow executions found.")
            return

        if not yes:
            typer.echo(
                f"Found {len(stale_observations)} stale executions. Re-run with --yes to restart them."
            )
            return

        restarted = 0
        for observation in stale_observations:
            _restart_execution(db, observation, terminate=terminate, signal=signal)
            restarted += 1

        typer.echo(f"Restarted {restarted} stale workflow executions.")


def run() -> None:
    """Execute the workflow task CLI."""
    app()


if __name__ == "__main__":
    run()
