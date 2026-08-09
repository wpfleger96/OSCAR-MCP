"""Add the user-declared ``timezone`` column to ``profiles``.

Stores an IANA timezone name (e.g. "America/New_York") declared by the user
for a profile.  Labeling metadata only (A6): device wall-clock timestamps stay
naive — the column never rewrites timestamps or fabricates UTC offsets.

For a fresh Alembic install (empty DB), ``001_baseline`` creates ``profiles``
from the current ``Base.metadata`` — which already includes ``timezone`` — so
this migration is a no-op there.  For an existing install stamped before this
revision, it adds the column.

Revision ID: 003_profile_timezone
Revises: 002_import_job_records_durability
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003_profile_timezone"
down_revision: str | Sequence[str] | None = "002_import_job_records_durability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "profiles"
_COLUMN = "timezone"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in existing_cols:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in existing_cols:
        op.drop_column(_TABLE, _COLUMN)
