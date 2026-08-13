"""Add Apple Health data tables: health_samples, health_nightly_summaries, health_ingest_tokens.

Stores raw Apple Health samples (sleep stages, SpO2, respiratory rate, breathing
disturbances), derived per-night sleep summaries, and machine-auth ingest tokens
for the push endpoint.  All three tables are profile-owned (FK to profiles, no
device FK), matching the mask-log domain pattern.

The ``health_samples`` dedup index uses COALESCE sentinels to close the NULL hole:
SQLite treats NULLs as distinct in UNIQUE constraints, so without sentinels a
category row (value_num IS NULL) would never conflict on re-import.

Revision ID: 010_health_data
Revises: 009_mask_log_optional_fields
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_health_data"
down_revision: str | Sequence[str] | None = "009_mask_log_optional_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_SAMPLES = "health_samples"
_TABLE_SUMMARIES = "health_nightly_summaries"
_TABLE_TOKENS = "health_ingest_tokens"


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    existing = sa_inspect(bind).get_table_names()

    if _TABLE_SAMPLES not in existing:
        op.create_table(
            _TABLE_SAMPLES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "profile_id",
                sa.Integer(),
                sa.ForeignKey("profiles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("record_type", sa.String(100), nullable=False),
            sa.Column("source_name", sa.String(200), nullable=False),
            sa.Column("source_version", sa.String(50), nullable=True),
            sa.Column("device_info", sa.Text(), nullable=True),
            sa.Column("start_time", sa.DateTime(), nullable=False),
            sa.Column("end_time", sa.DateTime(), nullable=False),
            sa.Column("value_text", sa.String(200), nullable=True),
            sa.Column("value_num", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(50), nullable=True),
            sa.Column("night_date", sa.Date(), nullable=False),
            sa.Column("utc_offset_seconds", sa.Integer(), nullable=True),
            sa.Column("ingest_channel", sa.String(20), nullable=False),
            sa.Column("imported_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "value_text IS NOT NULL OR value_num IS NOT NULL",
                name="chk_health_sample_value",
            ),
        )
        op.create_index(
            "uq_health_sample_dedup",
            _TABLE_SAMPLES,
            [
                "profile_id",
                "record_type",
                "source_name",
                "start_time",
                "end_time",
                sa.text("coalesce(value_text, '')"),
                sa.text("coalesce(value_num, -1.0)"),
            ],
            unique=True,
        )
        op.create_index(
            "ix_health_samples_profile_type_night",
            _TABLE_SAMPLES,
            ["profile_id", "record_type", "night_date"],
        )
        op.create_index(
            "ix_health_samples_profile_night_source",
            _TABLE_SAMPLES,
            ["profile_id", "night_date", "source_name"],
        )

    if _TABLE_SUMMARIES not in existing:
        op.create_table(
            _TABLE_SUMMARIES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "profile_id",
                sa.Integer(),
                sa.ForeignKey("profiles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("night_date", sa.Date(), nullable=False),
            sa.Column("preferred_source", sa.String(200), nullable=True),
            sa.Column("time_in_bed_seconds", sa.Float(), nullable=True),
            sa.Column("total_sleep_seconds", sa.Float(), nullable=True),
            sa.Column("core_seconds", sa.Float(), nullable=True),
            sa.Column("deep_seconds", sa.Float(), nullable=True),
            sa.Column("rem_seconds", sa.Float(), nullable=True),
            sa.Column("awake_seconds", sa.Float(), nullable=True),
            sa.Column("unspecified_seconds", sa.Float(), nullable=True),
            sa.Column("sleep_efficiency_pct", sa.Float(), nullable=True),
            sa.Column("stage_coverage_pct", sa.Float(), nullable=True),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "profile_id",
                "night_date",
                name="uq_health_nightly_summaries_profile_night",
            ),
        )

    if _TABLE_TOKENS not in existing:
        op.create_table(
            _TABLE_TOKENS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "profile_id",
                sa.Integer(),
                sa.ForeignKey("profiles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
            sa.Column("label", sa.String(100), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    existing = sa_inspect(bind).get_table_names()

    if _TABLE_TOKENS in existing:
        op.drop_table(_TABLE_TOKENS)
    if _TABLE_SUMMARIES in existing:
        op.drop_table(_TABLE_SUMMARIES)
    if _TABLE_SAMPLES in existing:
        op.drop_table(_TABLE_SAMPLES)
