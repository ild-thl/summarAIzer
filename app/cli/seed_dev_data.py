"""Typer-based CLI for seeding local development data."""

from __future__ import annotations

from datetime import datetime

import typer

from app.cli.common import db_session, get_database_url, load_environment
from app.database.models import APIKey, User
from app.security.auth import hash_api_key

load_environment()

app = typer.Typer(
    help="Seed development database with a test user and API key.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def seed_development_data() -> bool:
    """Create development test user and API key."""
    database_url = get_database_url()

    typer.echo("\n" + "=" * 70)
    typer.echo("SEED DEVELOPMENT DATA")
    typer.echo("=" * 70)

    typer.echo("\n[STEP 1] Connecting to database...")
    typer.echo(f"  Database: {database_url.split('@')[-1]}")

    try:
        with db_session() as db:
            typer.echo("✓ Connected to database")

            typer.echo("\n[STEP 2] Creating test user...")
            api_user = db.query(User).filter(User.username == "api_user").first()

            if api_user:
                typer.echo(f"✓ api_user already exists (ID: {api_user.id})")
            else:
                api_user = User(
                    username="api_user",
                    email="api_user@localhost",
                    type="api",
                    is_active=True,
                )
                db.add(api_user)
                db.flush()
                typer.echo(f"✓ Created api_user user (ID: {api_user.id})")

            typer.echo("\n[STEP 3] Creating test API key...")
            test_api_key = "test-api-key"
            key_hash = hash_api_key(test_api_key)
            existing_key = db.query(APIKey).filter(APIKey.key_hash == key_hash).first()

            if existing_key:
                typer.echo("✓ Test API key already exists")
            else:
                api_key = APIKey(
                    user_id=api_user.id,
                    key_hash=key_hash,
                    name="dev-api-user",
                    created_at=datetime.utcnow(),
                )
                db.add(api_key)
                typer.echo(f"✓ Created test API key: {test_api_key}")

            db.commit()
            typer.echo("\n✓ Database seeding completed successfully!")
            typer.echo("\n[SUMMARY]")
            typer.echo(f"  User: api_user (ID: {api_user.id})")
            typer.echo("  Type: API service account")
            typer.echo(f"  API Key: {test_api_key}")
            typer.echo(
                "\nYou can now use 'test-api-key' in SUMMARAIZER_API_KEY environment variable"
            )
            typer.echo("\nFor secure key rollover without losing access, use:")
            typer.echo(
                "  python -m app.cli api-keys "
                "keys rotate --username api_user --name secure-rotation"
            )
            typer.echo("Add --revoke-old only after clients switched to the new key.")
            return True
    except Exception as exc:
        typer.echo(f"✗ Failed to seed development data: {exc}", err=True)
        return False


@app.callback(invoke_without_command=True)
def seed(ctx: typer.Context) -> None:
    """Seed the local development database when invoked directly."""
    if ctx.invoked_subcommand is not None:
        return

    success = seed_development_data()
    typer.echo("\n" + "=" * 70 + "\n")
    if not success:
        raise typer.Exit(code=1)


def run() -> None:
    """Execute the Typer CLI."""
    app()


if __name__ == "__main__":
    run()
