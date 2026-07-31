"""Backfill absolute-instant columns for UTCDateTime contract (§3).

All absolute-instant columns in SNORE store naive UTC datetimes (SQLite has no
native timezone storage).  ``UTCDateTime`` re-attaches ``tzinfo=UTC`` on read, so
no data transformation is required on SQLite — the contract is enforced by the
Python type, not the column definition.

This migration exists to:
1. Document the column classification in the migration history.
2. Validate that no NULL values exist in non-nullable absolute-instant columns
   (a NULL would silently violate the contract on PostgreSQL TIMESTAMPTZ columns
   once the hosted milestone lands).
3. Serve as the anchor point for future ALTER TABLE … TYPE TIMESTAMPTZ statements
   on PostgreSQL (added in the hosted milestone migration).

The upgrade is a no-op on SQLite (render_as_batch is conditional on dialect).
The downgrade is a no-op everywhere — column type changes are schema-only.

Revision ID: c3f2a1b4d5e6
Revises: ab6a53d1327b
Create Date: 2026-08-01 00:00:01.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f2a1b4d5e6"
down_revision: str | Sequence[str] | None = "ab6a53d1327b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Absolute-instant columns that must not be NULL.
# Format: (table_name, column_name)
_NON_NULL_ABSOLUTE_COLUMNS = [
    ("profiles", "created_at"),
    ("profiles", "updated_at"),
    ("devices", "first_seen"),
    ("days", "created_at"),
    ("days", "updated_at"),
    ("sessions", "import_date"),
    ("analysis_results", "created_at"),
]

# Nullable absolute-instant columns (device.last_import may be NULL for
# devices that have not yet been imported against this version).
_NULLABLE_ABSOLUTE_COLUMNS = [
    ("devices", "last_import"),
]


def upgrade() -> None:
    """Document and validate the UTCDateTime column contract.

    On SQLite this migration validates that all non-nullable absolute-instant
    columns contain non-NULL values.  No column type changes are needed because
    UTCDateTime stores naive UTC strings on SQLite and re-attaches tzinfo on read.

    On PostgreSQL (future hosted milestone) this migration will be extended with
    ALTER TABLE ... ALTER COLUMN ... TYPE TIMESTAMPTZ USING col AT TIME ZONE 'UTC'
    statements.
    """
    conn = op.get_bind()

    # Validate that all non-nullable absolute-instant columns are fully populated.
    # A NULL in any of these columns would silently break the UTC contract on
    # PostgreSQL once the hosted milestone adds TIMESTAMPTZ columns.
    for table, column in _NON_NULL_ABSOLUTE_COLUMNS:
        result = conn.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")  # noqa: S608
        )
        null_count = result.scalar() or 0
        if null_count > 0:
            raise RuntimeError(
                f"UTCDateTime contract violation: {null_count} NULL value(s) found "
                f"in {table}.{column} — all absolute-instant columns must be "
                f"non-NULL before the UTC contract can be enforced."
            )


def downgrade() -> None:
    """No-op — the contract is enforced by the Python type, not DDL."""
    pass
