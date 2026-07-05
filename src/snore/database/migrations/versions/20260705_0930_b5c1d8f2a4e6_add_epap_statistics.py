"""Add EPAP statistics columns to statistics table

Revision ID: b5c1d8f2a4e6
Revises: a3f8e9c12b45
Create Date: 2026-07-05 09:30:00.000000

BiLevel/AirCurve devices report EPAP summary statistics (min/median/mean/95th/max),
but the initial migration omitted all five columns from the ``statistics`` table.
They were present in models.py and therefore in databases created via
``Base.metadata.create_all()`` (the historical ``snore db init`` path), but absent
from the alembic migration chain.

Each ``add_column`` is guarded against databases that were already created via
``create_all`` and already have these columns. The startup auto-migration feature
stamps such legacy databases at ``a3f8e9c12b45`` then runs ``upgrade head``, so
this migration will execute against them; the guard prevents a duplicate-column
error on that path.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision: str = "b5c1d8f2a4e6"
down_revision: str | Sequence[str] | None = "a3f8e9c12b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add epap_min/max/median/mean/95th to statistics, skipping pre-existing columns."""
    # Legacy databases created via Base.metadata.create_all() already have these
    # five columns. The guard here prevents a duplicate-column error when the
    # startup migration path stamps such a database at a3f8e9c12b45 and then
    # executes this revision.
    bind = op.get_bind()
    existing = {col["name"] for col in sa_inspect(bind).get_columns("statistics")}

    for col_name, col in (
        ("epap_min", sa.Column("epap_min", sa.Float(), nullable=True)),
        ("epap_max", sa.Column("epap_max", sa.Float(), nullable=True)),
        ("epap_median", sa.Column("epap_median", sa.Float(), nullable=True)),
        ("epap_mean", sa.Column("epap_mean", sa.Float(), nullable=True)),
        ("epap_95th", sa.Column("epap_95th", sa.Float(), nullable=True)),
    ):
        if col_name not in existing:
            op.add_column("statistics", col)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("statistics", "epap_95th")
    op.drop_column("statistics", "epap_mean")
    op.drop_column("statistics", "epap_median")
    op.drop_column("statistics", "epap_max")
    op.drop_column("statistics", "epap_min")
