"""Shared helpers for CLI tools."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def load_environment() -> None:
    """Load the nearest supported env file without overriding existing env vars."""
    env_paths = [
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[3] / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break


def get_database_url() -> str:
    """Return configured database URL or raise a user-facing error."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return database_url


@contextmanager
def db_session() -> Iterator[Session]:
    """Open and close a short-lived database session for CLI commands."""
    engine = create_engine(get_database_url())
    maker = sessionmaker(bind=engine)
    session = maker()
    try:
        yield session
    finally:
        session.close()
