"""Add lab and other session formats with NOT NULL constraint

Revision ID: e3456a75212d
Revises: 9fd5db95bfc6
Create Date: 2026-04-15 09:29:39.289064

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "e3456a75212d"
down_revision: Union[str, Sequence[str], None] = "9fd5db95bfc6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()

    if connection.dialect.name == "postgresql":
        # Step 1: Create a new enum type with all values (old + new)
        op.execute(
            text(
                "CREATE TYPE sessionformat_new AS ENUM ('INPUT', 'LIGHTNING_TALK', 'DISCUSSION', 'WORKSHOP', 'TRAINING', 'LAB', 'OTHER')"
            )
        )

        # Step 2: Convert column to new type with NULL -> 'OTHER' mapping
        op.execute(
            text(
                "ALTER TABLE sessions ALTER COLUMN session_format TYPE sessionformat_new USING COALESCE(session_format::text, 'OTHER')::sessionformat_new"
            )
        )

        # Step 3: Drop the old enum type
        op.execute(text("DROP TYPE sessionformat"))

        # Step 4: Rename the new enum to the original name
        op.execute(text("ALTER TYPE sessionformat_new RENAME TO sessionformat"))

    # Step 5: Make session_format NOT NULL
    op.alter_column(
        "sessions",
        "session_format",
        existing_type=postgresql.ENUM(
            "INPUT",
            "LIGHTNING_TALK",
            "DISCUSSION",
            "WORKSHOP",
            "TRAINING",
            "LAB",
            "OTHER",
            name="sessionformat",
        ),
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    connection = op.get_bind()

    if connection.dialect.name == "postgresql":
        # Step 1: Make session_format nullable first
        op.alter_column(
            "sessions",
            "session_format",
            existing_type=postgresql.ENUM(
                "INPUT",
                "LIGHTNING_TALK",
                "DISCUSSION",
                "WORKSHOP",
                "TRAINING",
                "LAB",
                "OTHER",
                name="sessionformat",
            ),
            nullable=True,
        )

        # Step 2: Create new enum with only old values
        op.execute(
            text(
                "CREATE TYPE sessionformat_new AS ENUM ('INPUT', 'LIGHTNING_TALK', 'DISCUSSION', 'WORKSHOP', 'TRAINING')"
            )
        )

        # Step 3: Convert column back, setting 'other' values back to NULL
        op.execute(
            text(
                "ALTER TABLE sessions ALTER COLUMN session_format TYPE sessionformat_new USING CASE WHEN session_format::text = 'OTHER' THEN NULL ELSE session_format::text END::sessionformat_new"
            )
        )

        # Step 4: Drop the old enum and rename new one
        op.execute(text("DROP TYPE sessionformat"))
        op.execute(text("ALTER TYPE sessionformat_new RENAME TO sessionformat"))

    # Note: PostgreSQL does not support removing enum values, so 'lab' and 'other'
    # will remain in the sessionformat enum type but won't be used
