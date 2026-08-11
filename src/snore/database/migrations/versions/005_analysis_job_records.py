"""Add the ``analysis_job_records`` table for analysis job durability.

Mirrors ``import_job_records``: persisted at RUNNING and terminal state
transitions so a server restart can detect orphaned in-progress rows and
either resume them or mark them failed.

``001_baseline`` skips ``analysis_job_records`` via its ``_EXCLUDE`` set, so
this migration creates the table on both fresh and existing installs; the
table-exists guard makes it idempotent.

Revision ID: 005_analysis_job_records
Revises: 004_session_mask_segments
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_analysis_job_records"
down_revision: str | Sequence[str] | None = "004_session_mask_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "analysis_job_records"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    if _TABLE in sa_inspect(bind).get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), unique=True, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("session_ids_json", sa.Text(), nullable=False),
        sa.Column("modes", sa.Text(), nullable=True),
        sa.Column("primary_mode", sa.String(50), nullable=True),
        sa.Column(
            "store_results", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column(
            "progress_completed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "progress_total", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('running','succeeded','failed','cancelled')",
            name="chk_analysis_job_record_state",
        ),
    )
    op.create_index("ix_analysis_job_records_owner_user_id", _TABLE, ["owner_user_id"])
    op.create_index(
        "ix_analysis_job_records_profile_state", _TABLE, ["profile_id", "state"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    if _TABLE in sa_inspect(bind).get_table_names():
        op.drop_table(_TABLE)
