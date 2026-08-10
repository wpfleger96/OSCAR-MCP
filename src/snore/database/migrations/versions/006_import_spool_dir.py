"""Add ``spool_dir_path`` to ``import_job_records``.

Stores the durable spool directory path so the server can resume interrupted
imports after a restart when the spool files survive on the persistent volume.

Revision ID: 006_import_spool_dir
Revises: 005_analysis_job_records
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_import_spool_dir"
down_revision: str | Sequence[str] | None = "005_analysis_job_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "import_job_records"
_COLUMN = "spool_dir_path"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    existing_cols = {c["name"] for c in sa_inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing_cols:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
