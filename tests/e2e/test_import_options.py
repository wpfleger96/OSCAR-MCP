"""Import-command option matrix, driven through the real binary.

`snore import` is the #1 user entry point and the most heavily refactored area
in the backend-simplification work, so its option surface gets dedicated
coverage: dry-run, force re-import, parallel/sequential parity, date filtering,
and the discontinuous (multi-segment) EDF path.
"""

from __future__ import annotations

import re


def _count_from_stats(stats_stdout: str, label: str) -> int:
    match = re.search(rf"{label}:\s*(\d+)", stats_stdout)
    assert match, f"could not find '{label}:' in db stats output:\n{stats_stdout}"
    return int(match.group(1))


def test_dry_run_imports_nothing(snore, fresh_db_path, resmed_sd):
    """`--dry-run` previews without writing sessions to the database."""
    result = snore(
        "import", str(resmed_sd), "--no-backup", "--all", "--dry-run", db=fresh_db_path
    )
    assert result.returncode == 0
    assert "dry" in result.stdout.lower()

    # Either no DB was created, or it was created empty — both mean "no writes".
    if fresh_db_path.exists():
        stats = snore("db", "stats", db=fresh_db_path)
        assert "Sessions: 0" in stats.stdout


def test_force_reimport_keeps_single_session(snore, imported_db, resmed_sd):
    """`--force` re-imports in place without duplicating the session."""
    result = snore(
        "import", str(resmed_sd), "--no-backup", "--all", "--force", db=imported_db
    )
    assert result.returncode == 0
    stats = snore("db", "stats", db=imported_db)
    assert "Sessions: 1" in stats.stdout


def test_parallel_and_sequential_produce_identical_db_state(snore, resmed_sd, tmp_path):
    """`--no-parallel` must yield the same persisted data as parallel parsing."""
    parallel_db = tmp_path / "parallel.db"
    sequential_db = tmp_path / "sequential.db"

    par = snore("import", str(resmed_sd), "--no-backup", "--all", db=parallel_db)
    seq = snore(
        "import",
        str(resmed_sd),
        "--no-backup",
        "--all",
        "--no-parallel",
        db=sequential_db,
    )
    assert par.returncode == 0 and seq.returncode == 0

    par_stats = snore("db", "stats", db=parallel_db).stdout
    seq_stats = snore("db", "stats", db=sequential_db).stdout

    for label in ("Sessions", "Events", "Waveforms"):
        assert _count_from_stats(par_stats, label) == _count_from_stats(
            seq_stats, label
        ), f"{label} count differs between parallel and sequential import"


def test_date_range_filter_excludes_out_of_range_nights(
    snore, fresh_db_path, resmed_sd
):
    """A date range that excludes the fixture night imports zero sessions."""
    result = snore(
        "import",
        str(resmed_sd),
        "--no-backup",
        "--all",
        "--from",
        "2030-01-01",
        "--to",
        "2030-12-31",
        db=fresh_db_path,
    )
    assert result.returncode == 0
    if fresh_db_path.exists():
        stats = snore("db", "stats", db=fresh_db_path)
        assert "Sessions: 0" in stats.stdout


def test_multi_segment_discontinuous_import(snore, multi_segment_sd, tmp_path):
    """The multi-segment (mask-off gaps) session imports and analyzes via CLI.

    This is the path that depends on discontinuous-EDF handling (the optional
    `edf-discontinuous` extra after the simplification PR). Behavior to preserve:
    the session imports with waveforms, and analysis runs to completion on it.
    """
    db = tmp_path / "multi.db"
    result = snore("import", str(multi_segment_sd), "--no-backup", "--all", db=db)
    assert result.returncode == 0, result.stderr or result.stdout
    assert db.exists()

    stats = snore("db", "stats", db=db).stdout
    assert _count_from_stats(stats, "Sessions") >= 1
    assert _count_from_stats(stats, "Waveforms") >= 1

    analyze = snore("analysis", "run", "--session-id", "1", db=db)
    assert analyze.returncode == 0, analyze.stderr or analyze.stdout


def test_no_analyze_skips_analysis_phase(snore, resmed_sd, tmp_path):
    """`--no-analyze` completes import without running the analysis phase.

    The import succeeds and a session exists in the DB, but no analysis result
    is stored — `analysis show` reports "no analysis found" for that session.
    """
    db = tmp_path / "no_analyze.db"
    result = snore(
        "import", str(resmed_sd), "--no-backup", "--all", "--no-analyze", db=db
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert db.exists()

    # Session landed in DB.
    stats = snore("db", "stats", db=db)
    assert "Sessions: 1" in stats.stdout

    # No analysis result — analysis show must fail (no stored result for session 1).
    shown = snore("analysis", "show", "--session-id", "1", db=db)
    assert shown.returncode != 0
    assert "no analysis found" in (shown.stdout + shown.stderr).lower()


def test_import_without_no_analyze_runs_analysis_phase(snore, resmed_sd, tmp_path):
    """A default import (no --no-analyze) runs analysis and stores a result.

    After `snore import`, `analysis show --session-id 1` must succeed — the
    import-time analysis phase populated the analysis_results table.  The
    breaths table must also be non-empty: if the segmenter wiring were broken
    and produced zero breaths, the analysis_results row would still exist but
    no breath rows would.
    """
    import sqlite3

    db = tmp_path / "with_analyze.db"
    result = snore("import", str(resmed_sd), "--no-backup", "--all", db=db)
    assert result.returncode == 0, result.stderr or result.stdout

    # Analysis result must be present (import-time analysis ran).
    shown = snore("analysis", "show", "--session-id", "1", db=db)
    assert shown.returncode == 0, shown.stderr or shown.stdout

    # breaths table must be non-empty — proves segmenter ran and wrote rows.
    con = sqlite3.connect(str(db))
    breath_count = con.execute("SELECT COUNT(*) FROM breaths").fetchone()[0]
    con.close()
    assert breath_count > 0, (
        f"Expected at least one breath row after real import; got {breath_count}. "
        "Segmenter wiring may be broken."
    )
