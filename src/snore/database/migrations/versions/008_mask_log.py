"""Add the ``mask_log`` table for the per-profile mask equipment log.

Stores user-entered mask history (brand, model, size, style, start date,
notes) so mask changes can be correlated with therapy outcomes.

For a fresh Alembic install (empty DB), ``001_baseline`` creates the table
from ``Base.metadata`` (excluded from baseline via ``_EXCLUDE``), so this
migration handles existing installs only.

Revision ID: 008_mask_log
Revises: 007_str_extras
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008_mask_log"
down_revision: str | Sequence[str] | None = "007_str_extras"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "mask_log"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    if _TABLE in sa_inspect(bind).get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("size", sa.String(50), nullable=True),
        sa.Column("style", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "style IN ('pillows','nasal','full_face')", name="chk_mask_style"
        ),
        sa.CheckConstraint("length(brand) > 0", name="chk_mask_brand"),
        sa.CheckConstraint("length(model) > 0", name="chk_mask_model"),
    )
    op.create_index(
        "ix_mask_log_profile_start_date", _TABLE, ["profile_id", "start_date"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    if _TABLE in sa_inspect(bind).get_table_names():
        op.drop_table(_TABLE)
