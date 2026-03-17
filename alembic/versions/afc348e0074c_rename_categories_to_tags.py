"""rename_categories_to_tags

Revision ID: afc348e0074c
Revises: afc348e0074b
Create Date: 2026-03-17 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "afc348e0074c"
down_revision: Union[str, Sequence[str], None] = "afc348e0074b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - rename categories column to tags."""
    # Rename the column
    op.alter_column("sessions", "categories", new_column_name="tags")


def downgrade() -> None:
    """Downgrade schema - rename tags column back to categories."""
    # Rename the column back
    op.alter_column("sessions", "tags", new_column_name="categories")
