"""Add the ``mask_on_segments`` column to ``sessions``.

Stores the ascending [start_offset_s, end_offset_s] mask-on intervals of a
(possibly merged) session as JSON, in session offset seconds.  NULL means
unknown (OSCAR imports, data imported before this revision); a single-segment
session stores ``[[0.0, duration]]`` so "known, no gaps" stays distinguishable
from unknown.

For a fresh Alembic install (empty DB), ``001_baseline`` creates ``sessions``
from the current ``Base.metadata`` — which already includes
``mask_on_segments`` — so this migration is a no-op there.  For an existing
install stamped before this revision, it adds the column.

Revision ID: 004_session_mask_segments
Revises: 003_profile_timezone
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004_session_mask_segments"
down_revision: str | Sequence[str] | None = "003_profile_timezone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "sessions"
_COLUMN = "mask_on_segments"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in existing_cols:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in existing_cols:
        op.drop_column(_TABLE, _COLUMN)
