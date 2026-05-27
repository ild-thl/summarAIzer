"""Typer-based administrative CLI for SummarAIzer API key management."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from app.cli.common import db_session, load_environment
from app.database.models import APIKey, User
from app.security.auth import hash_api_key

load_environment()

app = typer.Typer(
    help="Admin CLI for SummarAIzer API key management",
    no_args_is_help=True,
)
users_app = typer.Typer(help="Manage users", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys", no_args_is_help=True)
app.add_typer(users_app, name="users")
app.add_typer(keys_app, name="keys")


@dataclass
class UserSelector:
    """Selection criteria for resolving one concrete user."""

    user_id: int | None = None
    username: str | None = None
    email: str | None = None


def _run_db_command(command: Callable[[Session], None]) -> None:
    """Run a DB-backed CLI command with consistent error handling."""
    try:
        with db_session() as db:
            command(db)
    except (LookupError, ValueError, RuntimeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _resolve_user(db: Session, selector: UserSelector) -> User:
    """Resolve exactly one user based on CLI selector flags."""
    provided = [
        selector.user_id is not None,
        bool(selector.username),
        bool(selector.email),
    ]
    if sum(provided) != 1:
        raise ValueError("Provide exactly one of --user-id, --username, or --email")

    query = db.query(User)
    if selector.user_id is not None:
        user = query.filter(User.id == selector.user_id).first()
    elif selector.username:
        user = query.filter(User.username == selector.username).first()
    else:
        user = query.filter(User.email == selector.email).first()

    if user is None:
        raise LookupError("User not found")

    return user


def _fmt_datetime(value: datetime | None) -> str:
    """Format datetime as ISO string for terminal output."""
    return value.isoformat() if value else "-"


def _create_key_for_user(db: Session, user: User, name: str | None) -> tuple[APIKey, str]:
    """Create a fresh secure key and return DB record plus plain key."""
    plain_key = secrets.token_urlsafe(32)
    db_key = APIKey(
        user_id=user.id,
        key_hash=hash_api_key(plain_key),
        name=name,
    )
    db.add(db_key)
    db.flush()
    return db_key, plain_key


@users_app.command("list")
def users_list(
    active_only: Annotated[
        bool,
        typer.Option("--active-only", help="List active users only"),
    ] = False,
) -> None:
    """List users for key management operations."""

    def command(db: Session) -> None:
        query = db.query(User).order_by(User.id.asc())
        if active_only:
            query = query.filter(User.is_active.is_(True))

        users = query.all()
        if not users:
            typer.echo("No users found.")
            return

        typer.echo("id\tusername\temail\ttype\tactive\tcreated_at")
        for user in users:
            typer.echo(
                f"{user.id}\t{user.username}\t{user.email or '-'}\t{user.type}\t"
                f"{str(bool(user.is_active)).lower()}\t{_fmt_datetime(user.created_at)}"
            )

    _run_db_command(command)


@keys_app.command("list")
def keys_list(
    user_id: Annotated[int | None, typer.Option("--user-id", help="Filter by user id")] = None,
    username: Annotated[
        str | None,
        typer.Option("--username", help="Filter by username"),
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help="Filter by email")] = None,
    include_revoked: Annotated[
        bool,
        typer.Option("--include-revoked", help="Include revoked keys in output"),
    ] = False,
) -> None:
    """List API keys, optionally scoped to a single user."""

    def command(db: Session) -> None:
        query = (
            db.query(APIKey, User).join(User, User.id == APIKey.user_id).order_by(APIKey.id.asc())
        )

        if user_id is not None or username or email:
            user = _resolve_user(
                db,
                UserSelector(user_id=user_id, username=username, email=email),
            )
            query = query.filter(APIKey.user_id == user.id)

        if not include_revoked:
            query = query.filter(APIKey.deleted_at.is_(None))

        rows = query.all()
        if not rows:
            typer.echo("No API keys found.")
            return

        typer.echo("key_id\tuser_id\tusername\tname\tstatus\tcreated_at\tlast_used_at\tdeleted_at")
        for key, user in rows:
            status = "revoked" if key.deleted_at else "active"
            typer.echo(
                f"{key.id}\t{user.id}\t{user.username}\t{key.name or '-'}\t{status}\t"
                f"{_fmt_datetime(key.created_at)}\t{_fmt_datetime(key.last_used_at)}\t"
                f"{_fmt_datetime(key.deleted_at)}"
            )

    _run_db_command(command)


@keys_app.command("create")
def keys_create(
    user_id: Annotated[int | None, typer.Option("--user-id", help="Target user id")] = None,
    username: Annotated[
        str | None,
        typer.Option("--username", help="Target username"),
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help="Target email")] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Optional key display name"),
    ] = None,
) -> None:
    """Create API key for a user without revoking old keys."""

    def command(db: Session) -> None:
        user = _resolve_user(db, UserSelector(user_id=user_id, username=username, email=email))
        normalized_name = name.strip() if isinstance(name, str) and name.strip() else None
        db_key, plain_key = _create_key_for_user(db, user, normalized_name)
        db.commit()

        typer.echo("API key created.")
        typer.echo(f"user_id: {user.id}")
        typer.echo(f"username: {user.username}")
        typer.echo(f"key_id: {db_key.id}")
        typer.echo(f"key_name: {db_key.name or '-'}")
        typer.echo(f"api_key: {plain_key}")
        typer.echo("note: store this key now; it cannot be retrieved again")

    _run_db_command(command)


@keys_app.command("revoke")
def keys_revoke(
    key_id: Annotated[int, typer.Option("--key-id", help="API key id")],
) -> None:
    """Revoke API key by id."""

    def command(db: Session) -> None:
        key = db.query(APIKey).filter(APIKey.id == key_id).first()
        if key is None:
            raise LookupError(f"API key with id {key_id} not found")

        if key.deleted_at is not None:
            typer.echo(f"API key {key.id} is already revoked at {_fmt_datetime(key.deleted_at)}")
            return

        key.deleted_at = datetime.utcnow()
        db.commit()
        typer.echo(f"Revoked API key {key.id}.")

    _run_db_command(command)


@keys_app.command("rotate")
def keys_rotate(
    user_id: Annotated[int | None, typer.Option("--user-id", help="Target user id")] = None,
    username: Annotated[
        str | None,
        typer.Option("--username", help="Target username"),
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help="Target email")] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Optional new key name"),
    ] = None,
    revoke_old: Annotated[
        bool,
        typer.Option(
            "--revoke-old",
            help="Revoke all existing active keys after creating the new key",
        ),
    ] = False,
) -> None:
    """Rotate keys safely by creating a new key and optionally revoking active old keys."""

    def command(db: Session) -> None:
        user = _resolve_user(db, UserSelector(user_id=user_id, username=username, email=email))
        normalized_name = name.strip() if isinstance(name, str) and name.strip() else None
        new_key, plain_key = _create_key_for_user(db, user, normalized_name)

        revoked_count = 0
        if revoke_old:
            active_old_keys = (
                db.query(APIKey)
                .filter(
                    APIKey.user_id == user.id,
                    APIKey.deleted_at.is_(None),
                    APIKey.id != new_key.id,
                )
                .all()
            )
            for old_key in active_old_keys:
                old_key.deleted_at = datetime.utcnow()
                revoked_count += 1

        db.commit()

        typer.echo("API key rotated.")
        typer.echo(f"user_id: {user.id}")
        typer.echo(f"username: {user.username}")
        typer.echo(f"new_key_id: {new_key.id}")
        typer.echo(f"new_key_name: {new_key.name or '-'}")
        typer.echo(f"api_key: {plain_key}")
        if revoke_old:
            typer.echo(f"revoked_old_keys: {revoked_count}")
        else:
            typer.echo("revoked_old_keys: 0")
            typer.echo("note: old keys remain active; revoke explicitly when migration is complete")

    _run_db_command(command)


def run() -> None:
    """Execute the Typer CLI."""
    app()


if __name__ == "__main__":
    run()
