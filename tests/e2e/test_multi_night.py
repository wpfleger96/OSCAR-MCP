"""Cross-night breadth: the surface a single imported night can't exercise.

These run against ``multi_night_db`` — the device night plus the four real
recorded nights composed into one import (5 sessions / 5 days spanning
2024-06 → 2025-10, all real data). They cover multi-date listing, date-range
filtering, batch analysis, and multi-session export.

Assertions are pinned to the deterministic contents of these fixed fixtures.
"""

from __future__ import annotations

import json
import re

# The five imported nights, by the START date shown in `session list`.
START_DATES = ["2024-06-21", "2025-01-10", "2025-08-08", "2025-09-10", "2025-10-25"]
# The four 2025 nights only (a 2025-bounded range excludes the 2024 device night).
DATES_2025 = [d for d in START_DATES if d.startswith("2025")]


def _count_from_stats(stdout: str, label: str) -> int:
    match = re.search(rf"{label}:\s*(\d+)", stdout)
    assert match, f"could not find '{label}:' in:\n{stdout}"
    return int(match.group(1))


def test_multi_night_import_shape(snore, multi_night_db):
    """The composed import yields the expected real, multi-date dataset."""
    stats = snore("db", "stats", db=multi_night_db).stdout
    assert _count_from_stats(stats, "Sessions") == 5
    assert _count_from_stats(stats, "Days") == 5
    # Events and waveforms populate across the real nights (not just one).
    assert _count_from_stats(stats, "Events") == 28
    assert _count_from_stats(stats, "Waveforms") == 55
    assert "2024-06-21 to 2025-10-25" in stats


def test_session_list_shows_every_night(snore, multi_night_db):
    listing = snore("session", "list", "--limit", "0", db=multi_night_db)
    assert listing.returncode == 0
    for date in START_DATES:
        assert date in listing.stdout, f"missing {date} in session list"


def test_date_range_filter_selects_only_in_range_nights(snore, multi_night_db):
    """A 2025-bounded range returns the four 2025 nights, not the 2024 one."""
    listing = snore(
        "session",
        "list",
        "--from",
        "2025-01-01",
        "--to",
        "2025-12-31",
        "--limit",
        "0",
        db=multi_night_db,
    )
    assert listing.returncode == 0
    rows = [ln for ln in listing.stdout.splitlines() if "22231974465" in ln]
    assert len(rows) == 4
    assert "2024-06-21" not in listing.stdout
    for date in DATES_2025:
        assert date in listing.stdout


def test_batch_analysis_over_date_range(snore, multi_night_db):
    """`analysis run --from/--to` analyzes exactly the in-range nights."""
    result = snore(
        "analysis",
        "run",
        "--from",
        "2025-01-01",
        "--to",
        "2025-12-31",
        db=multi_night_db,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "4/4" in result.stdout or "Successful: 4" in result.stdout
    assert "Failed: 0" in result.stdout

    listed = snore("analysis", "list", db=multi_night_db)
    assert listed.returncode == 0
    # Four nights analyzed (✓); the un-analyzed 2024 night remains (✗).
    assert listed.stdout.count("✓") == 4
    assert "✗" in listed.stdout


def test_csv_export_has_one_row_per_night(snore, multi_night_db, tmp_path):
    out = tmp_path / "csv"
    result = snore("export", "csv", "--output", str(out), db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout
    lines = (out / "sessions.csv").read_text().strip().splitlines()
    assert len(lines) == 1 + 5  # header + five nights


def test_json_export_includes_all_nights(snore, multi_night_db, tmp_path):
    out = tmp_path / "out.json"
    result = snore("export", "json", "--output", str(out), db=multi_night_db)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(out.read_text())
    assert payload["session_count"] == 5
    assert len(payload["sessions"]) == 5
