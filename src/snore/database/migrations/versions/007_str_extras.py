"""Add STR extra statistics columns and CSL-sourced PB columns.

Imports all remaining ResMed STR.edf daily statistics that SNORE previously
dropped: UAI/AI complete the apnea-index set; 95th/max percentiles for RR,
TV, and MV; I:E and Ti percentiles for VAuto; flow/blow-pressure/blow-flow
percentiles; climate-sensor medians; SpO2 percentiles; APAP-only RIN/CSR;
VAuto-only SpontCyc%; and mask-event count.

Also wires the pre-existing ``leak_percentile_70`` ORM column into
``STR_SUMMARY_SIGNALS`` so it is now actually populated from STR.edf
(the column existed but was never sourced from the STR loader).

Revision ID: 007_str_extras
Revises: 006_import_spool_dir
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_str_extras"
down_revision: str | Sequence[str] | None = "006_import_spool_dir"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "statistics"

# (column_name, sa_type) — all nullable
_NEW_COLUMNS: list[tuple[str, sa.types.TypeEngine[float]]] = [
    # SpO2 daily summaries (both device types)
    ("spo2_median", sa.Float()),
    ("spo2_95th", sa.Float()),
    # Apnea indices
    ("uai", sa.Float()),
    ("ai", sa.Float()),
    # APAP-only
    ("rin", sa.Float()),
    ("csr_pct", sa.Float()),
    # VAuto-only
    ("spont_cyc_pct", sa.Float()),
    # Respiratory rate extras (95th only; max already existed before this migration)
    ("respiratory_rate_95th", sa.Float()),
    # Tidal volume extras (95th only; max already existed before this migration)
    ("tidal_volume_95th", sa.Float()),
    # Minute ventilation extras (95th only; max already existed before this migration)
    ("minute_ventilation_95th", sa.Float()),
    # I:E ratio (VAuto-only)
    ("ie_ratio_median", sa.Float()),
    ("ie_ratio_95th", sa.Float()),
    ("ie_ratio_max", sa.Float()),
    # Inspiratory time (VAuto-only)
    ("ti_median", sa.Float()),
    ("ti_95th", sa.Float()),
    ("ti_max", sa.Float()),
    # Flow percentiles
    ("flow_5th", sa.Float()),
    ("flow_95th", sa.Float()),
    # Blow-side pressure / flow
    ("blow_press_5th", sa.Float()),
    ("blow_press_95th", sa.Float()),
    ("blow_flow_median", sa.Float()),
    # Climate / humidifier stats
    ("amb_humidity_median", sa.Float()),
    ("hum_temp_median", sa.Float()),
    ("htube_temp_median", sa.Float()),
    ("htube_pow_median", sa.Float()),
    ("hum_pow_median", sa.Float()),
    # Mask events count
    ("mask_events", sa.Float()),
]


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    existing_cols = {c["name"] for c in sa_inspect(bind).get_columns(_TABLE)}
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing_cols:
            op.add_column(_TABLE, sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    for col_name, _ in _NEW_COLUMNS:
        op.drop_column(_TABLE, col_name)
