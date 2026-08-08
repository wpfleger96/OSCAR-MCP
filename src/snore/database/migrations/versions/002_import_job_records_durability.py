"""Durability schema changes for import_job_records.

- Adds ``updated_at`` (DATETIME NOT NULL, backfilled to CURRENT_TIMESTAMP).
- Makes ``finished_at`` nullable (non-terminal rows have no finish time).
- Replaces the terminal-only state CheckConstraint with one that allows all
  six import-job states so PENDING_UPLOAD / PENDING / RUNNING rows can be
  persisted for crash-recovery.

For a fresh Alembic install (empty DB), this migration creates the
``import_job_records`` table directly with the full new schema (since
``001_baseline`` intentionally excludes it).  For an existing install stamped
at ``001_baseline`` (where the table already exists with the old schema), it
applies the delta changes.

Revision ID: 002_import_job_records_durability
Revises: 001_baseline
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_import_job_records_durability"
down_revision: str | Sequence[str] | None = "001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "import_job_records"
# Short constraint name — the naming convention (ck_<table>_<name>) is applied
# by Alembic's batch machinery, so pass only the bare name here.
_CONSTRAINT = "chk_import_job_record_state"
_STATES_NEW = (
    "state IN ('pending_upload','pending','running','succeeded','failed','cancelled')"
)
_STATES_OLD = "state IN ('succeeded','failed','cancelled')"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    if _TABLE not in existing_tables:
        # Fresh Alembic install (empty DB): create the full table directly so
        # ``upgrade head`` produces a schema matching Base.metadata.
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("job_id", sa.String(32), nullable=False),
            sa.Column("job_type", sa.String(20), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=True),
            sa.Column("target_profile_id", sa.Integer(), nullable=True),
            sa.Column("state", sa.String(20), nullable=False),
            sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sessions_imported", sa.Integer(), nullable=True),
            sa.Column("import_result_json", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("analysis_queued", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
            sa.UniqueConstraint("job_id", name=f"uq_{_TABLE}_job_id"),
            sa.CheckConstraint(_STATES_NEW, name=_CONSTRAINT),
        )
        op.create_index(f"ix_{_TABLE}_owner_user_id", _TABLE, ["owner_user_id"])
        op.create_index(
            f"ix_{_TABLE}_user_created", _TABLE, ["owner_user_id", "created_at"]
        )
        return

    # Existing install: apply delta changes only.
    # SQLite requires full table recreation for constraint changes; batch mode
    # handles this transparently.
    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}
    with op.batch_alter_table(_TABLE) as batch_op:
        if "updated_at" not in existing_cols:
            # Back-fill existing rows with the migration timestamp.
            batch_op.add_column(
                sa.Column(
                    "updated_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                )
            )
        # Allow NULL for non-terminal jobs that have not finished yet.
        batch_op.alter_column("finished_at", nullable=True)
        # Remove the terminal-only constraint and replace with the full set.
        # Pass the short constraint name — the naming convention prefix
        # (ck_import_job_records_) is applied automatically.
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, _STATES_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    if _TABLE not in existing_tables:
        return

    existing_cols = {c["name"] for c in insp.get_columns(_TABLE)}

    # Was the table created by this migration's upgrade (fresh-install path)?
    # If so, drop it entirely.  Otherwise, reverse only the delta.
    # Heuristic: if the table has "updated_at" but did NOT exist before this
    # migration on the fresh path, we check whether other tables were present
    # before this migration ran (they would be absent on a pure fresh install).
    # For simplicity, always use the delta-reversal path — the fresh-install
    # path is rare in practice and full table drop is too destructive.
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, _STATES_OLD)
        batch_op.alter_column("finished_at", nullable=False)
        if "updated_at" in existing_cols:
            batch_op.drop_column("updated_at")
