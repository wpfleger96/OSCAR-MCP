"""Add ``started_at`` column to ``import_job_records``.

Import job records tracked ``created_at`` and ``finished_at`` but never the
moment the job began RUNNING, so the UI could not show how long a job took or
when it actually started (analysis job records already carry ``started_at``).
The column is additive and nullable: existing rows and jobs that never reach
RUNNING (e.g. cancelled while pending) leave it null.

Revision ID: 014_import_job_started_at
Revises: 013_totp
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014_import_job_started_at"
down_revision: str | Sequence[str] | None = "013_totp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "import_job_records"
_COLUMN = "started_at"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in existing_cols:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in existing_cols:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
