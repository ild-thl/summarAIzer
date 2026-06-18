"""add session owners and ownership claims

Revision ID: f1a2c3d4e5f6
Revises: c7e3d4a9b1f2
Create Date: 2026-06-16 12:00:00.000000

"""

from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1a2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c7e3d4a9b1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    op.create_table(
        "session_owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("added_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "user_id", name="uq_session_owner_pair"),
    )
    op.create_index(op.f("ix_session_owners_id"), "session_owners", ["id"], unique=False)
    op.create_index(
        op.f("ix_session_owners_session_id"),
        "session_owners",
        ["session_id"],
        unique=False,
    )
    op.create_index(op.f("ix_session_owners_user_id"), "session_owners", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_session_owners_added_by_user_id"),
        "session_owners",
        ["added_by_user_id"],
        unique=False,
    )

    op.create_table(
        "session_ownership_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("requester_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("request_note", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_session_ownership_claims_id"),
        "session_ownership_claims",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_ownership_claims_session_id"),
        "session_ownership_claims",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_ownership_claims_requester_user_id"),
        "session_ownership_claims",
        ["requester_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_ownership_claims_status"),
        "session_ownership_claims",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_ownership_claims_reviewed_by_user_id"),
        "session_ownership_claims",
        ["reviewed_by_user_id"],
        unique=False,
    )

    # Partial unique index to ensure only one pending claim per (session_id, requester_user_id)
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_claim_pending ON session_ownership_claims(session_id, requester_user_id) WHERE status = 'pending'"
        )
    )

    now = datetime.utcnow()
    bind.execute(
        sa.text("""
            INSERT INTO session_owners (session_id, user_id, added_by_user_id, created_at)
            SELECT s.id, s.owner_id, NULL, :created_at
            FROM sessions s
            WHERE s.owner_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """),
        {"created_at": now},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_session_ownership_claims_reviewed_by_user_id"),
        table_name="session_ownership_claims",
    )
    op.drop_index(op.f("ix_session_ownership_claims_status"), table_name="session_ownership_claims")
    op.drop_index(
        op.f("ix_session_ownership_claims_requester_user_id"),
        table_name="session_ownership_claims",
    )
    op.drop_index(
        op.f("ix_session_ownership_claims_session_id"),
        table_name="session_ownership_claims",
    )
    op.drop_index(op.f("ix_session_ownership_claims_id"), table_name="session_ownership_claims")
    # drop partial unique index if exists
    op.execute(sa.text("DROP INDEX IF EXISTS uq_session_claim_pending"))
    op.drop_table("session_ownership_claims")

    op.drop_index(op.f("ix_session_owners_added_by_user_id"), table_name="session_owners")
    op.drop_index(op.f("ix_session_owners_user_id"), table_name="session_owners")
    op.drop_index(op.f("ix_session_owners_session_id"), table_name="session_owners")
    op.drop_index(op.f("ix_session_owners_id"), table_name="session_owners")
    op.drop_table("session_owners")
