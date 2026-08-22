"""Add the ``validation_runs`` table for persisted validator runs.

Unlike the ``*_job_records`` durability mirrors, this table *is* the result of a
validator run: the background job (or the synchronous POST for sync validator
types) writes its state and full ``report_json`` here directly.  Runs are kept
so every future algorithm change is measurable run-vs-run across engine
versions; the dedup/comparison key is
``(profile_id, validator_type, date_from, date_to, engine_identity_json,
validator_params_json)``.

The table-exists guard makes this migration idempotent on both fresh and
existing installs (``001_baseline`` does not know about this table).

Revision ID: 016_validation_runs
Revises: 015_statistics_device_indices
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016_validation_runs"
down_revision: str | Sequence[str] | None = "015_statistics_device_indices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "validation_runs"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    if _TABLE in sa_inspect(bind).get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), unique=True, nullable=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("validator_type", sa.String(20), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("engine_identity_json", sa.Text(), nullable=False),
        sa.Column("validator_params_json", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="chk_validation_run_state",
        ),
    )
    op.create_index("ix_validation_runs_owner_user_id", _TABLE, ["owner_user_id"])
    op.create_index(
        "ix_validation_runs_profile_type_created",
        _TABLE,
        ["profile_id", "validator_type", "created_at"],
    )
    op.create_index(
        "ix_validation_runs_dedup",
        _TABLE,
        ["profile_id", "validator_type", "date_from", "date_to"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    if _TABLE in sa_inspect(bind).get_table_names():
        op.drop_table(_TABLE)
