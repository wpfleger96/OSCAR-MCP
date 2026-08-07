"""drop tautological chk_profile_deleting constraint

Revision ID: a1b2c3d4e5f6
Revises: dab8ad625898
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "dab8ad625898"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the always-true chk_profile_deleting constraint.

    ``deleting_at IS NULL OR deleting_at IS NOT NULL`` is a tautology — it
    holds for every row regardless of the column's value and enforces nothing.
    SQLite does not support DROP CONSTRAINT directly; batch mode recreates
    the table.

    The constraint's stored name varies by how the schema was created:
    baseline-migration DBs carry the env.py naming-convention expansion
    (``ck_profiles_chk_profile_deleting``) while pre-migration DBs created
    via ``Base.metadata.create_all`` carry the bare ``chk_profile_deleting``.
    Reflect the actual name and drop that; ``op.f()`` marks it final so the
    naming convention cannot re-expand it.  Skip cleanly when neither form
    exists (already dropped).
    """
    inspector = sa.inspect(op.get_bind())
    names = {c["name"] for c in inspector.get_check_constraints("profiles")}
    target = next((n for n in names if n and n.endswith("chk_profile_deleting")), None)
    if target is None:
        return
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(target), type_="check")


def downgrade() -> None:
    """Restore the (no-op) constraint for schema reversibility."""
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "chk_profile_deleting",
            "deleting_at IS NULL OR deleting_at IS NOT NULL",
        )
