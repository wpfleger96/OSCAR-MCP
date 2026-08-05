"""add user preferences

Revision ID: a3f2c8e91b05
Revises: dab8ad625898
Create Date: 2026-08-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

import snore.database.types

# revision identifiers, used by Alembic.
revision: str = "a3f2c8e91b05"
down_revision: str | Sequence[str] | None = "dab8ad625898"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "preferences",
            snore.database.types.ValidatedJSONWithDefault(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("preferences")
