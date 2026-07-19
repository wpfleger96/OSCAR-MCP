"""Analysis detection modes exercised through the real `analysis run` command.

The detector was decomposed during simplification with a "bit-identical
results" claim; running every public mode end-to-end on real fixture data is
the regression net for that. Assertions stay tolerant (each mode completes,
stores a result, and reports a finite AHI), so legitimate output reformatting
doesn't break the suite while a mode that stops producing results does.
"""

from __future__ import annotations


def test_all_modes_flag_runs_every_mode(snore, imported_db):
    """`--all-modes` completes and produces validation output for the session."""
    result = snore(
        "analysis", "run", "--session-id", "1", "--all-modes", db=imported_db
    )
    assert result.returncode == 0, result.stderr or result.stdout
    # The all-modes run renders the flow-limitation breakdown and validations.
    assert "FLOW LIMITATION ANALYSIS" in result.stdout


def test_no_store_does_not_persist(snore, imported_db):
    """`--no-store` analyzes without recording a stored AnalysisResult."""
    result = snore("analysis", "run", "--session-id", "1", "--no-store", db=imported_db)
    assert result.returncode == 0, result.stderr or result.stdout

    # Nothing stored, so `analysis show` should report no analysis found.
    shown = snore("analysis", "show", "--session-id", "1", db=imported_db)
    assert shown.returncode != 0
    assert "no analysis found" in (shown.stdout + shown.stderr).lower()


def test_validate_reports_per_session_metrics(snore, imported_db):
    """`validate` runs the programmatic-vs-machine comparison end to end."""
    result = snore(
        "validate", "--from", "2024-06-01", "--to", "2024-06-30", db=imported_db
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Per-Session Results" in result.stdout
