"""Add compound index on sessions(device_id, start_time).

The ``_find_overlapping`` query filters by ``device_id`` equality and a
``start_time``/``end_time`` range predicate.  Without this index every import
performs a full ``sessions`` table scan scoped to the device — a cost that
grows linearly with session count.  PR #251 also increases session-row counts
(diagnostic blips become standalone sessions), so the scan's cost grows faster
than it did before.

The model-side ``Index`` declaration covers fresh DBs via ``create_all``; this
migration covers pre-existing DBs on upgrade.  Both produce the same index name
(``ix_sessions_device_id_start_time``) so the ``test_migration_schema_drift``
parity check stays green.

Revision ID: 012_session_time_index
Revises: 011_oauth_connect_user_id
"""

from collections.abc import Sequence

from alembic import op

revision: str = "012_session_time_index"
down_revision: str | Sequence[str] | None = "011_oauth_connect_user_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_sessions_device_id_start_time"
_TABLE = "sessions"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    existing_indexes = {ix["name"] for ix in sa_inspect(bind).get_indexes(_TABLE)}
    if _INDEX not in existing_indexes:
        op.create_index(_INDEX, _TABLE, ["device_id", "start_time"])


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    existing_indexes = {ix["name"] for ix in sa_inspect(bind).get_indexes(_TABLE)}
    if _INDEX in existing_indexes:
        op.drop_index(_INDEX, table_name=_TABLE)
