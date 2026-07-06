"""add sessions day_id index

Revision ID: ab6a53d1327b
Revises: 0b38e809682e
Create Date: 2026-07-05 16:42:48.825744

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab6a53d1327b"
down_revision: str | Sequence[str] | None = "0b38e809682e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_sessions_day_id", "sessions", ["day_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_sessions_day_id", table_name="sessions")
