"""Add TOTP 2FA columns to ``users`` and create ``totp_recovery_codes`` table.

Three columns are added to ``users``:
- ``totp_secret`` (String 64) — Base32 TOTP secret.
  Non-null with ``totp_enabled_at`` null = pending unconfirmed setup.
  Non-null with ``totp_enabled_at`` set = active 2FA enrollment.
  Stored as plaintext Base32 by design: the symmetric secret must be
  recoverable for verification; defense is DB file access control.
  Deliberately NOT encrypted with SNORE_SESSION_SECRET so that secret
  rotation cannot brick all enrollments.
- ``totp_enabled_at`` (UTCDateTime) — timestamp when TOTP was confirmed active.
- ``totp_last_used_step`` (Integer) — last verified time-step for replay prevention.

A new ``totp_recovery_codes`` table is created with a ``user_id`` FK and an
index on that column.  ``code_hash`` is String(255) to store the full argon2
encoded hash (salt + parameters + digest), not a fixed-length SHA-256 hex.

Revision ID: 013_totp
Revises: 012_session_time_index
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "013_totp"
down_revision: str | Sequence[str] | None = "012_session_time_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USERS_TABLE = "users"
_RECOVERY_TABLE = "totp_recovery_codes"
_RECOVERY_INDEX = "ix_totp_recovery_codes_user_id"

_NEW_COLS: list[tuple[str, sa.Column[Any]]] = [
    ("totp_secret", sa.Column("totp_secret", sa.String(64), nullable=True)),
    (
        "totp_enabled_at",
        sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "totp_last_used_step",
        sa.Column("totp_last_used_step", sa.Integer(), nullable=True),
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)

    # Add new columns to users if absent.
    existing_cols = {c["name"] for c in insp.get_columns(_USERS_TABLE)}
    for col_name, col_def in _NEW_COLS:
        if col_name not in existing_cols:
            op.add_column(_USERS_TABLE, col_def)

    # Create totp_recovery_codes table + index if absent.
    if _RECOVERY_TABLE not in insp.get_table_names():
        op.create_table(
            _RECOVERY_TABLE,
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("code_hash", sa.String(255), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name="pk_totp_recovery_codes"),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name="fk_totp_recovery_codes_user_id_users",
                ondelete="CASCADE",
            ),
        )
        op.create_index(_RECOVERY_INDEX, _RECOVERY_TABLE, ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)

    # Drop totp_recovery_codes table and its index if present.
    if _RECOVERY_TABLE in insp.get_table_names():
        existing_indexes = {ix["name"] for ix in insp.get_indexes(_RECOVERY_TABLE)}
        if _RECOVERY_INDEX in existing_indexes:
            op.drop_index(_RECOVERY_INDEX, table_name=_RECOVERY_TABLE)
        op.drop_table(_RECOVERY_TABLE)

    # Remove the three columns from users using batch_alter_table (SQLite-portable).
    existing_cols = {c["name"] for c in insp.get_columns(_USERS_TABLE)}
    cols_to_drop = [name for name, _ in _NEW_COLS if name in existing_cols]
    if cols_to_drop:
        with op.batch_alter_table(_USERS_TABLE) as batch_op:
            for col_name in cols_to_drop:
                batch_op.drop_column(col_name)
