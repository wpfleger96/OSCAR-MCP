"""drop tautological chk_profile_deleting constraint

Revision ID: a1b2c3d4e5f6
Revises: dab8ad625898
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

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
    the table.  The env.py naming convention expanded the baseline migration's
    ``chk_profile_deleting`` to ``ck_profiles_chk_profile_deleting`` in the
    DB; calling drop_constraint("chk_profile_deleting") causes Alembic to
    expand via convention and look up the full name.
    """
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.drop_constraint("chk_profile_deleting", type_="check")


def downgrade() -> None:
    """Restore the (no-op) constraint for schema reversibility."""
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "chk_profile_deleting",
            "deleting_at IS NULL OR deleting_at IS NOT NULL",
        )
