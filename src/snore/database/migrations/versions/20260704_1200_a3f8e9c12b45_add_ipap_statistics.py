"""Add IPAP statistics columns to statistics table

Revision ID: a3f8e9c12b45
Revises: 102cf96663ea
Create Date: 2026-07-04 12:00:00.000000

BiLevel/AirCurve devices report inspiratory pressure (IPAP) summary stats via
STR.edf (TgtIPAP.50, TgtIPAP.95, TgtIPAP.Max). These columns were missing from
the statistics table, causing the ResMed parser's ``hasattr`` guard to silently
drop the values on every import. Existing BiLevel/AirCurve imports will have
NULL for these columns and need to be re-imported to backfill the data.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f8e9c12b45"
down_revision: str | Sequence[str] | None = "102cf96663ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("statistics", sa.Column("ipap_median", sa.Float(), nullable=True))
    op.add_column("statistics", sa.Column("ipap_95th", sa.Float(), nullable=True))
    op.add_column("statistics", sa.Column("ipap_max", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("statistics", "ipap_max")
    op.drop_column("statistics", "ipap_95th")
    op.drop_column("statistics", "ipap_median")
