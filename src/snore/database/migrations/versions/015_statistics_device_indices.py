"""Add device-reported index columns to ``statistics``.

ResMed STR daily summaries report AHI/OAI/CAI/HI computed by the device, but
``finalize_statistics`` recomputes those same indices from parsed events and
overwrote the device values.  These four additive, nullable columns preserve
the device-reported indices alongside the computed ``ahi/oai/cai/hi`` so both
are retained.

For a fresh Alembic install (empty DB), ``001_baseline`` creates ``statistics``
from the current ``Base.metadata`` — which already includes these columns — so
this migration is a no-op there.  For an existing install stamped before this
revision, it adds the columns.

Revision ID: 015_statistics_device_indices
Revises: 014_import_job_started_at
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015_statistics_device_indices"
down_revision: str | Sequence[str] | None = "014_import_job_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "statistics"
_COLUMNS = ("ahi_device", "oai_device", "cai_device", "hi_device")


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    for column in _COLUMNS:
        if column not in existing_cols:
            op.add_column(_TABLE, sa.Column(column, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    # batch_alter_table is the SQLite-portable way to drop a column
    # (table-copy recreate).
    with op.batch_alter_table(_TABLE) as batch_op:
        for column in _COLUMNS:
            if column in existing_cols:
                batch_op.drop_column(column)
