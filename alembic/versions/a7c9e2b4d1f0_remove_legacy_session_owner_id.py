"""remove legacy session owner_id

Revision ID: a7c9e2b4d1f0
Revises: f1a2c3d4e5f6
Create Date: 2026-06-17 11:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a7c9e2b4d1f0"
down_revision: Union[str, Sequence[str], None] = "f1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Final backfill from legacy column before dropping it.
    op.execute("""
        INSERT INTO session_owners (session_id, user_id, added_by_user_id, created_at)
        SELECT s.id, s.owner_id, NULL, COALESCE(s.updated_at, s.created_at, NOW())
        FROM sessions s
        WHERE s.owner_id IS NOT NULL
        ON CONFLICT (session_id, user_id) DO NOTHING
        """)

    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_owner_id_fkey")
    op.execute("DROP INDEX IF EXISTS ix_sessions_owner_id")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS owner_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("sessions", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_sessions_owner_id"), "sessions", ["owner_id"], unique=False)
    op.create_foreign_key(
        "sessions_owner_id_fkey",
        "sessions",
        "users",
        ["owner_id"],
        ["id"],
    )

    # Restore legacy owner_id from the first owner link per session.
    op.execute("""
        WITH first_owners AS (
            SELECT DISTINCT ON (so.session_id)
                so.session_id,
                so.user_id
            FROM session_owners so
            ORDER BY so.session_id, so.created_at ASC, so.id ASC
        )
        UPDATE sessions s
        SET owner_id = fo.user_id
        FROM first_owners fo
        WHERE fo.session_id = s.id
        """)
