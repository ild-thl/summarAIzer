"""add session external id relation

Revision ID: c7e3d4a9b1f2
Revises: 7d21f6b31f77
Create Date: 2026-05-29 15:00:00.000000

"""

from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7e3d4a9b1f2"
down_revision: Union[str, Sequence[str], None] = "7d21f6b31f77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _extract_sessionize_id(uri: str | None) -> str | None:
    """Extract a legacy Sessionize ID from the current URI convention '<id>-<slug>'."""
    if not uri or "-" not in uri:
        return None

    candidate = uri.split("-", 1)[0].strip()
    if not candidate or not candidate.isdigit():
        return None
    return candidate


def _dedupe_sessions_by_legacy_external_id(connection) -> None:
    """Delete older duplicate sessions per event and Sessionize legacy URI prefix.

    We keep the most recently touched session (updated_at, then created_at, then id)
    and delete older duplicates so URI changes in the source do not leave stale rows
    that later conflict with the unique (event_id, uri) constraint during sync upserts.
    """
    connection.execute(sa.text("""
            WITH candidates AS (
                SELECT
                    s.id,
                    s.event_id,
                    split_part(s.uri, '-', 1) AS legacy_external_id,
                    COALESCE(s.updated_at, s.created_at) AS last_touched_at,
                    s.created_at
                FROM sessions s
                WHERE
                    s.event_id IS NOT NULL
                    AND s.uri ~ '^[0-9]+-.+'
            ),
            ranked AS (
                SELECT
                    c.id,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.event_id, c.legacy_external_id
                        ORDER BY c.last_touched_at DESC NULLS LAST, c.created_at DESC NULLS LAST, c.id DESC
                    ) AS rn
                FROM candidates c
            )
            DELETE FROM sessions s
            USING ranked r
            WHERE s.id = r.id AND r.rn > 1
            """))


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "session_external_ids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label", "external_id", name="uq_session_external_id_global"),
        sa.UniqueConstraint(
            "session_id",
            "label",
            "external_id",
            name="uq_session_external_id_per_session",
        ),
    )
    op.create_index(
        op.f("ix_session_external_ids_id"),
        "session_external_ids",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_external_ids_session_id"),
        "session_external_ids",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_external_ids_label"),
        "session_external_ids",
        ["label"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_external_ids_external_id"),
        "session_external_ids",
        ["external_id"],
        unique=False,
    )

    connection = op.get_bind()
    _dedupe_sessions_by_legacy_external_id(connection)

    sessions = connection.execute(sa.text("SELECT id, uri FROM sessions")).fetchall()
    now = datetime.utcnow()

    for session_id, uri in sessions:
        legacy_external_id = _extract_sessionize_id(uri)
        if legacy_external_id is None:
            continue

        connection.execute(
            sa.text("""
                INSERT INTO session_external_ids
                    (session_id, label, external_id, created_at, updated_at)
                VALUES
                    (:session_id, :label, :external_id, :created_at, :updated_at)
                ON CONFLICT DO NOTHING
                """),
            {
                "session_id": session_id,
                "label": "sessionize.com",
                "external_id": legacy_external_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_session_external_ids_external_id"), table_name="session_external_ids")
    op.drop_index(op.f("ix_session_external_ids_label"), table_name="session_external_ids")
    op.drop_index(op.f("ix_session_external_ids_session_id"), table_name="session_external_ids")
    op.drop_index(op.f("ix_session_external_ids_id"), table_name="session_external_ids")
    op.drop_table("session_external_ids")
