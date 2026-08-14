"""Add ``connect_user_id`` to ``oauth_attempts`` to support the Google connect flow.

A non-NULL ``connect_user_id`` marks an OAuth attempt as a connect-kind flow:
the authenticated user at ``connect_user_id`` is requesting to link their Google
identity.  ``kind`` stays ``"login"`` so the existing ``chk_oauth_kind`` CHECK
constraint holds on pre-existing DBs.

SQLite's ``ADD COLUMN`` does not support FK constraints, so the FK clause exists
only on fresh ``create_all`` DBs (same tradeoff ``_sync_additive_schema``
documents for other nullable additive columns).  ``resolve_connect`` in
``_google_resolution.py`` guards against the user-not-found case explicitly.

Revision ID: 011_oauth_connect_user_id
Revises: 010_health_data
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011_oauth_connect_user_id"
down_revision: str | Sequence[str] | None = "010_health_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "oauth_attempts"
_COLUMN = "connect_user_id"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in existing_cols:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in existing_cols:
        # batch_alter_table is the SQLite-portable way to drop a column
        # (table-copy recreate), matching the 003 migration's convention.
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
