"""Purge fabricated settings and sentinel statistics from no-usage STR.edf days

Revision ID: c4d2f7a8b1e9
Revises: b5c1d8f2a4e6
Create Date: 2026-07-05 10:00:00.000000

The ResMed STR.edf parser read daily summary records for every calendar day in
the file, including days with no device usage. On those days every channel holds
a negative sentinel value (e.g. -1). The parser passed those sentinels through
range validation for boolean/enum channels (which have no range check), so it
wrote settings rows and statistics columns from garbage data.

The parser bug is fixed in the same changeset: the parser now discards STR
records whose values are all negative/NaN. This migration purges the historical
fabrications:

1. Settings rows whose session_id belongs to a fabricated group: a group that
   contains ONLY the 8 fallback enum/boolean keys (no numeric pressure keys,
   which were filtered by range validation) and whose mask_type and epr_mode
   are both 'Unknown' (the -1 sentinel misses every enum map entry and falls
   through to the 'Unknown' default).

2. Statistics columns supplemented from STR.edf where the stored value is
   negative. All STR-sourced quantities are physically non-negative, so a
   negative value is unambiguously a sentinel.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d2f7a8b1e9"
down_revision: str | Sequence[str] | None = "b5c1d8f2a4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Purge fabricated settings groups and NULL out negative sentinel statistics."""
    # Cleanup 1: delete settings rows belonging to fabricated groups.
    # A fabricated group has exactly the 8 fallback enum/boolean keys and no
    # others, with mask_type = 'Unknown' and epr_mode = 'Unknown'.
    op.execute(
        """
        DELETE FROM settings WHERE session_id IN (
            SELECT session_id FROM settings
            GROUP BY session_id
            HAVING COUNT(*) = SUM(CASE WHEN "key" IN (
                'climate_control','epr_mode','humidity_enabled','mask_type',
                'mode','ramp_enabled','smart_start','tube_temp_enabled'
            ) THEN 1 ELSE 0 END)
               AND MAX(CASE WHEN "key" = 'mask_type' THEN value END) = 'Unknown'
               AND MAX(CASE WHEN "key" = 'epr_mode'  THEN value END) = 'Unknown'
        )
        """
    )

    # Cleanup 2: NULL out negative sentinel values in STR-supplemented stat
    # columns. Each column is physically non-negative; negative == sentinel.
    for col in (
        "pressure_median",
        "pressure_95th",
        "pressure_max",
        "epap_median",
        "epap_95th",
        "epap_max",
        "ipap_median",
        "ipap_95th",
        "ipap_max",
        "leak_median",
        "leak_95th",
        "leak_max",
        "respiratory_rate_mean",
        "tidal_volume_mean",
        "minute_ventilation_mean",
        "ahi",
        "oai",
        "cai",
        "hi",
    ):
        op.execute(f"UPDATE statistics SET {col} = NULL WHERE {col} < 0")


def downgrade() -> None:
    """No-op: deleted settings rows and NULLed statistics cannot be recovered."""
    pass
