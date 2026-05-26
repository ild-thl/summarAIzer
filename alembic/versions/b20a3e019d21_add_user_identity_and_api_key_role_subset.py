"""add user identity metadata and api key delegated role subset

Revision ID: b20a3e019d21
Revises: 89c13d4cc639
Create Date: 2026-05-22 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b20a3e019d21"
down_revision: Union[str, Sequence[str], None] = "89c13d4cc639"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("keycloak_sub", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("roles", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("users", sa.Column("groups", sa.JSON(), nullable=False, server_default="[]"))
    op.create_index(op.f("ix_users_keycloak_sub"), "users", ["keycloak_sub"], unique=True)

    op.add_column(
        "api_keys",
        sa.Column(
            "allowed_roles",
            sa.JSON(),
            nullable=True,
            comment="Optional delegated role subset; NULL means full owner role delegation",
        ),
    )

    op.alter_column("users", "roles", server_default=None)
    op.alter_column("users", "groups", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("api_keys", "allowed_roles")

    op.drop_index(op.f("ix_users_keycloak_sub"), table_name="users")
    op.drop_column("users", "groups")
    op.drop_column("users", "roles")
    op.drop_column("users", "keycloak_sub")
