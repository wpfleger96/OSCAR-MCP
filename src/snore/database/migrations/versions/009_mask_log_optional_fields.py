"""Make ``mask_log`` identity columns nullable to support partial mask entries.

Users may want to save a mask entry before they have all fields filled in (e.g.
they know the brand but not yet the model, or they want to log a start date
without specifying style).  Making ``brand``, ``model``, ``style``, and
``start_date`` nullable lets the API accept and persist partial entries without
forcing the user to provide placeholder values.

The three named CHECK constraints are replaced with null-tolerant equivalents
so that NULL rows pass without violating the constraint.  (SQLite CHECK
constraints technically pass on NULL already, but explicit IS NULL guards
document intent — and batch-mode rebuild recreates all constraints anyway.)

Revision ID: 009_mask_log_optional_fields
Revises: 008_mask_log
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_mask_log_optional_fields"
down_revision: str | Sequence[str] | None = "008_mask_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "mask_log"

_STYLE_NEW = "style IS NULL OR style IN ('pillows','nasal','full_face')"
_BRAND_NEW = "brand IS NULL OR length(brand) > 0"
_MODEL_NEW = "model IS NULL OR length(model) > 0"

_STYLE_OLD = "style IN ('pillows','nasal','full_face')"
_BRAND_OLD = "length(brand) > 0"
_MODEL_OLD = "length(model) > 0"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    cols = {c["name"]: c for c in insp.get_columns(_TABLE)}

    # Idempotency: if brand is already nullable the batch rebuild already ran.
    brand = cols.get("brand")
    if brand is not None and brand["nullable"]:
        return

    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.alter_column("brand", existing_type=sa.String(100), nullable=True)
        batch_op.alter_column("model", existing_type=sa.String(150), nullable=True)
        batch_op.alter_column("style", existing_type=sa.String(20), nullable=True)
        batch_op.alter_column("start_date", existing_type=sa.Date(), nullable=True)

        batch_op.drop_constraint("chk_mask_style", type_="check")
        batch_op.drop_constraint("chk_mask_brand", type_="check")
        batch_op.drop_constraint("chk_mask_model", type_="check")

        batch_op.create_check_constraint("chk_mask_style", _STYLE_NEW)
        batch_op.create_check_constraint("chk_mask_brand", _BRAND_NEW)
        batch_op.create_check_constraint("chk_mask_model", _MODEL_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    insp = sa_inspect(bind)
    cols = {c["name"]: c for c in insp.get_columns(_TABLE)}

    # Idempotency: if brand is already NOT NULL the reversal already ran.
    brand = cols.get("brand")
    if brand is not None and not brand["nullable"]:
        return

    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.alter_column("brand", existing_type=sa.String(100), nullable=False)
        batch_op.alter_column("model", existing_type=sa.String(150), nullable=False)
        batch_op.alter_column("style", existing_type=sa.String(20), nullable=False)
        batch_op.alter_column("start_date", existing_type=sa.Date(), nullable=False)

        batch_op.drop_constraint("chk_mask_style", type_="check")
        batch_op.drop_constraint("chk_mask_brand", type_="check")
        batch_op.drop_constraint("chk_mask_model", type_="check")

        batch_op.create_check_constraint("chk_mask_style", _STYLE_OLD)
        batch_op.create_check_constraint("chk_mask_brand", _BRAND_OLD)
        batch_op.create_check_constraint("chk_mask_model", _MODEL_OLD)
