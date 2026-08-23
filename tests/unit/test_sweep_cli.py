"""CLI robustness tests for ``snore sweep-thresholds``.

These exercise option validation that fires before any DB access — the
``--top`` range and the empty-parameter-grid guard — so no database or profile
resolution is patched.
"""

from __future__ import annotations

from click.testing import CliRunner, Result

from snore.cli.commands.sweep import sweep_thresholds


def _invoke(args: list[str]) -> Result:
    return CliRunner().invoke(
        sweep_thresholds,
        ["--from", "2025-06-01", "--to", "2025-06-30", *args],
    )


def test_empty_knob_override_reports_empty_grid():
    # --flg-low "" zeroes the grid; the message must name that, not "no data".
    result = _invoke(["--target", "flg", "--flg-low", ""])
    assert result.exit_code != 0
    assert "Empty parameter grid" in result.output
    assert "flg_low_threshold" in result.output
    assert "No data to sweep" not in result.output


def test_top_zero_rejected_by_range():
    result = _invoke(["--target", "flg", "--top", "0"])
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_top_negative_rejected_by_range():
    result = _invoke(["--target", "re", "--top", "-5"])
    assert result.exit_code != 0
    assert "not in the range" in result.output
