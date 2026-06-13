"""Therapy-statistics views over the multi-night dataset.

`stats` aggregates across days, so it needs more than one night to be meaningful.
Numeric values on the recorded nights are sparse, so these assert command-level
success plus the structural output (period rows, month labels) — enough to catch
a stats/aggregation regression without coupling to exact figures.
"""

from __future__ import annotations

import pytest

# Months present in the multi-night dataset (by Day date / noon-to-noon).
EXPECTED_MONTHS = ["Jun 2024", "Jan 2025", "Aug 2025", "Sep 2025", "Oct 2025"]


def test_stats_summary_runs(snore, multi_night_db):
    result = snore("stats", db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Events" in result.stdout


def test_stats_monthly_breakdown_lists_every_month(snore, multi_night_db):
    result = snore("stats", "--period", "month", db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout
    for month in EXPECTED_MONTHS:
        assert month in result.stdout, f"missing month row {month!r}"
    # The fixed device night's AHI is deterministic in its month row.
    assert "17.6" in result.stdout


@pytest.mark.parametrize("period", ["week", "month"])
def test_stats_period_variants_run(snore, multi_night_db, period):
    result = snore("stats", "--period", period, db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout


def test_stats_trend_runs(snore, multi_night_db):
    result = snore("stats", "--trend", db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout


def test_stats_days_window_runs(snore, multi_night_db):
    result = snore("stats", "--days", "30", db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout
