"""add last_login_at to users

Revision ID: f7e8d9c0b1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

import snore.database.types

# revision identifiers, used by Alembic.
revision: str = "f7e8d9c0b1a2"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable last_login_at column to users."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_login_at",
                snore.database.types.UTCDateTime(),
                nullable=True,
            )
        )


def downgrade() -> None:
    """Drop last_login_at column from users."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("last_login_at")
