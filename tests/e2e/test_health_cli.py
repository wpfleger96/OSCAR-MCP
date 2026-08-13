"""End-to-end tests for the `snore health` command group.

Tests drive the real binary (no in-process imports) and exercise:
- `health import` (dry-run and real), idempotency, invalid path
- `health list` and `health show` after a real import
- `health token create/list/revoke` round-trip
"""

from __future__ import annotations

import re
import sqlite3

from pathlib import Path

import pytest

HEALTH_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "health_data"
# Date present in the fixture's export.xml that has multiple sleep stages.
FIXTURE_NIGHT = "2024-01-15"


@pytest.fixture
def health_db(tmp_path, e2e_home):
    """Fresh database with Apple Health fixture imported once."""
    from tests.e2e import helpers

    db = tmp_path / "health.db"
    result = helpers.run_snore(
        "health", "import", str(HEALTH_FIXTURE), db=db, home=e2e_home
    )
    assert result.returncode == 0, (
        f"fixture import failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return db


def test_health_import_dry_run_writes_nothing(snore, fresh_db_path):
    """`--dry-run` previews records without persisting any rows."""
    result = snore(
        "health", "import", str(HEALTH_FIXTURE), "--dry-run", db=fresh_db_path
    )
    assert result.returncode == 0, result.stderr or result.stdout
    combined = result.stdout + result.stderr
    assert "dry run" in combined.lower() or "would insert" in combined.lower()

    if fresh_db_path.exists():
        con = sqlite3.connect(str(fresh_db_path))
        count = con.execute("SELECT COUNT(*) FROM health_samples").fetchone()[0]
        con.close()
        assert count == 0, f"Expected 0 rows after dry-run; got {count}"


def test_health_import_real_inserts_rows(snore, fresh_db_path):
    """A real import exits 0 and reports inserted > 0."""
    result = snore("health", "import", str(HEALTH_FIXTURE), db=fresh_db_path)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Inserted" in result.stdout

    con = sqlite3.connect(str(fresh_db_path))
    count = con.execute("SELECT COUNT(*) FROM health_samples").fetchone()[0]
    con.close()
    assert count > 0, "Expected health_samples rows after real import"


def test_health_import_idempotent(snore, tmp_path):
    """A second identical import reports 0 new records."""
    db = tmp_path / "health.db"
    first = snore("health", "import", str(HEALTH_FIXTURE), db=db)
    assert first.returncode == 0, first.stderr or first.stdout

    second = snore("health", "import", str(HEALTH_FIXTURE), db=db)
    assert second.returncode == 0, second.stderr or second.stdout
    assert "0 new records" in second.stdout


def test_health_import_invalid_path_exits_nonzero(snore, fresh_db_path):
    """A path that does not exist produces a nonzero exit and a clear message."""
    result = snore("health", "import", "/nonexistent/path/export.zip", db=fresh_db_path)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not exist" in combined.lower() or "error" in combined.lower()


def test_health_list_shows_fixture_nights(snore, health_db):
    """`health list` shows the nights from the fixture (newest first)."""
    result = snore("health", "list", db=health_db)
    assert result.returncode == 0, result.stderr or result.stdout
    assert FIXTURE_NIGHT in result.stdout


def test_health_list_respects_limit(snore, health_db):
    """`--limit 1` returns at most 1 data row."""
    result = snore("health", "list", "--limit", "1", db=health_db)
    assert result.returncode == 0, result.stderr or result.stdout
    # Count date-like lines (YYYY-MM-DD) in stdout. The header row says "Date",
    # not a real date, so only actual data rows contribute matches.
    date_rows = re.findall(r"\d{4}-\d{2}-\d{2}", result.stdout)
    assert len(date_rows) <= 1, f"Expected ≤1 date row with --limit 1; got: {date_rows}"


def test_health_show_displays_intervals_and_totals(snore, health_db):
    """`health show <date>` renders sleep intervals and summary totals."""
    result = snore("health", "show", FIXTURE_NIGHT, db=health_db)
    assert result.returncode == 0, result.stderr or result.stdout
    # The totals block labels are always printed.
    assert "Total sleep" in result.stdout
    assert "Time in bed" in result.stdout
    assert "Efficiency" in result.stdout


def test_health_show_unknown_date_exits_nonzero(snore, health_db):
    """`health show` with no data for the date exits nonzero."""
    result = snore("health", "show", "2000-01-01", db=health_db)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "no health data" in combined.lower()


def test_health_token_create_list_revoke(snore, tmp_path):
    """Token round-trip: create → list → revoke → confirm revoked in list."""
    db = tmp_path / "tokens.db"
    # Initialize the DB and profile via a health import.
    init = snore("health", "import", str(HEALTH_FIXTURE), db=db)
    assert init.returncode == 0, init.stderr or init.stdout

    # Create a token.
    create = snore("health", "token", "create", "--label", "test-token", db=db)
    assert create.returncode == 0, create.stderr or create.stdout
    assert "Store this token now" in create.stdout
    assert "Token:" in create.stdout

    m = re.search(r"Token ID:\s*(\d+)", create.stdout)
    assert m, f"Could not find 'Token ID:' in output:\n{create.stdout}"
    token_id = m.group(1)

    # List tokens — should show the new token with its label.
    listing = snore("health", "token", "list", db=db)
    assert listing.returncode == 0, listing.stderr or listing.stdout
    assert token_id in listing.stdout
    assert "test-token" in listing.stdout

    # Revoke the token.
    revoke = snore("health", "token", "revoke", token_id, db=db)
    assert revoke.returncode == 0, revoke.stderr or revoke.stdout
    assert "revoked" in (revoke.stdout + revoke.stderr).lower()

    # List again — token should still appear (kept for audit trail).
    listing2 = snore("health", "token", "list", db=db)
    assert listing2.returncode == 0
    assert token_id in listing2.stdout


def test_health_token_revoke_bogus_id_exits_nonzero(snore, tmp_path):
    """Revoking a non-existent token ID exits nonzero with a clear message."""
    db = tmp_path / "tokens2.db"
    init = snore("health", "import", str(HEALTH_FIXTURE), db=db)
    assert init.returncode == 0, init.stderr or init.stdout

    result = snore("health", "token", "revoke", "99999", db=db)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not found" in combined.lower()
