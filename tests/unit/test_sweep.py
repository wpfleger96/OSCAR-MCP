"""Unit tests for the offline threshold-sweep harness.

The engine is exercised through its pure seams — ``score_fl_arrays`` (the
extraction regression), ``enumerate_grid``, ``evaluate_grid``, and
``export_sweep_csv`` — driven over hand-built in-memory caches with no DB.  A
planted-signal case proves the correct knob setting ranks first.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from snore.validation.fl_validator import score_fl_arrays
from snore.validation.sweep import (
    DEFAULT_GRIDS,
    NOT_SWEEPABLE_NOTICE,
    FlgSessionArrays,
    ProxySessionArrays,
    SweepData,
    enumerate_grid,
    evaluate_grid,
    export_sweep_csv,
)

# ---------------------------------------------------------------------------
# score_fl_arrays — the extracted pure scoring core
# ---------------------------------------------------------------------------


class TestScoreFlArrays:
    def _arrays(
        self, mid_insp: list[float], flg: list[float]
    ) -> tuple[np.ndarray, ...]:
        """Build (mid_insp, flatness, class_weight, rule_matched, flg, session_flg)."""
        n = len(mid_insp)
        return (
            np.array(mid_insp, dtype=np.float64),
            np.zeros(n, dtype=np.float64),
            np.full(n, np.nan, dtype=np.float64),
            np.zeros(n, dtype=bool),
            np.array(flg, dtype=np.float64),
            np.array(flg, dtype=np.float64),
        )

    def test_monotonic_severity_gives_unit_auc(self):
        # flattening_severity = 1 - mid_insp is monotone increasing with FLG.
        mid_insp = [0.9, 0.8, 0.4, 0.3]
        flg = [0.1, 0.2, 0.6, 0.7]
        scores = score_fl_arrays(*self._arrays(mid_insp, flg))
        assert scores is not None
        assert scores.n_breaths_compared == 4
        assert scores.auc_low == pytest.approx(1.0)

    def test_no_aligned_pairs_returns_none(self):
        args = self._arrays([0.5, 0.5], [np.nan, np.nan])
        assert score_fl_arrays(*args) is None

    def test_low_threshold_override_changes_auc(self):
        # An inversion between FLG 0.5 and 0.6 makes the breakpoint matter.
        mid_insp = [0.9, 0.1, 0.5, 0.05]  # severity = 0.1, 0.9, 0.5, 0.95
        flg = [0.1, 0.5, 0.6, 0.9]
        args = self._arrays(mid_insp, flg)
        at_45 = score_fl_arrays(*args, flg_low_threshold=0.45)
        at_55 = score_fl_arrays(*args, flg_low_threshold=0.55)
        assert at_45 is not None and at_55 is not None
        assert at_45.auc_low == pytest.approx(1.0)
        assert at_55.auc_low == pytest.approx(0.75)

    def test_class_weight_low_threshold_override_changes_auc(self):
        # The class-weight AUC path honours the same FLG breakpoint override.
        # Weights inverted against FLG between 0.5 and 0.6 make the breakpoint
        # matter (all breaths rule-matched with a known class weight).
        mid_insp = np.full(4, 0.5, dtype=np.float64)
        flatness = np.zeros(4, dtype=np.float64)
        class_weight = np.array([0.1, 0.9, 0.5, 0.95], dtype=np.float64)
        rule_matched = np.ones(4, dtype=bool)
        flg = np.array([0.1, 0.5, 0.6, 0.9], dtype=np.float64)
        at_45 = score_fl_arrays(
            mid_insp,
            flatness,
            class_weight,
            rule_matched,
            flg,
            flg,
            flg_low_threshold=0.45,
        )
        at_55 = score_fl_arrays(
            mid_insp,
            flatness,
            class_weight,
            rule_matched,
            flg,
            flg,
            flg_low_threshold=0.55,
        )
        assert at_45 is not None and at_55 is not None
        assert at_45.auc_class_low == pytest.approx(1.0)
        assert at_55.auc_class_low == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Grid enumeration
# ---------------------------------------------------------------------------


class TestEnumerateGrid:
    def test_cartesian_product_size_and_order(self):
        grid = {"a": [1, 2, 3], "b": [10, 20]}
        combos = enumerate_grid(grid)
        assert len(combos) == 6
        assert combos[0] == {"a": 1, "b": 10}
        assert list(combos[0].keys()) == ["a", "b"]
        # every combination is present exactly once
        assert {(c["a"], c["b"]) for c in combos} == {
            (a, b) for a in [1, 2, 3] for b in [10, 20]
        }

    def test_empty_grid_yields_single_empty_combo(self):
        assert enumerate_grid({}) == [{}]


# ---------------------------------------------------------------------------
# Helpers to build proxy sessions with a known number of RERA-proxy events
# ---------------------------------------------------------------------------


def _proxy_session(
    therapy_date: date,
    n_events: int,
    *,
    duration_hours: float = 8.0,
    machine_starts: list[float] | None = None,
    fl_class: int = 4,
) -> ProxySessionArrays:
    """One session whose FL-run proxy fires exactly ``n_events`` times.

    Each event is the breath triplet ``[fl_class, fl_class, recovery]`` — a
    length-2 FL run followed by an ``is_recovery_breath`` follower.
    """
    flow_class: list[int | None] = []
    is_recovery: list[bool | None] = []
    peak: list[float | None] = []
    start_offset: list[float] = []
    t = 0.0
    for _ in range(n_events):
        flow_class += [fl_class, fl_class, 1]
        is_recovery += [False, False, True]
        peak += [20.0, 20.0, 40.0]
        start_offset += [t, t + 2.0, t + 4.0]
        t += 30.0
    return ProxySessionArrays(
        session_id=int(therapy_date.strftime("%Y%m%d")),
        therapy_date=therapy_date,
        duration_hours=duration_hours,
        flow_class=flow_class,
        is_recovery_breath=is_recovery,
        peak_flow_lpm=peak,
        start_offset_s=start_offset,
        machine_starts=machine_starts if machine_starts is not None else [],
    )


# ---------------------------------------------------------------------------
# RE target — planted signal must rank first
# ---------------------------------------------------------------------------


class TestReTargetPlantedSignal:
    def test_correct_threshold_ranks_first(self):
        # One event at offset 0; a machine RE at 0 within tolerance. At
        # fl_class_threshold=4 the run qualifies (sensitivity 1.0); at 6 no
        # breath is flow-limited (proxy fires nothing, sensitivity 0).
        session = _proxy_session(
            date(2024, 1, 1), n_events=1, machine_starts=[0.0], fl_class=4
        )
        data = SweepData(target="re", proxy_sessions=[session])
        grid = {
            "fl_class_threshold": [4, 6],
            "min_fl_run_length": [2],
            "recovery_amplitude_margin": [0.20],
        }
        result = evaluate_grid(data, grid)

        top = result.rows[0]
        assert top.knobs["fl_class_threshold"] == 4
        assert top.metrics["sensitivity"] == pytest.approx(1.0)
        assert top.is_default  # 4 / 2 / 0.20 are the current constants
        # The strict-threshold config fires nothing and ranks last.
        assert result.rows[-1].knobs["fl_class_threshold"] == 6
        assert result.rows[-1].metrics["sensitivity"] == pytest.approx(0.0)
        # Chance floor is reported and the winner clears it.
        floor = result.reference["chance_precision_floor"]
        assert floor is not None
        assert top.objective == pytest.approx(1.0 - floor)

    def test_degenerate_low_threshold_inflates_proxy_density(self):
        # min_fl_run_length=1 with a permissive class threshold makes far more
        # runs qualify — the reported proxy density must expose that.
        session = _proxy_session(date(2024, 1, 1), n_events=3, machine_starts=[0.0])
        data = SweepData(target="re", proxy_sessions=[session])
        grid = {
            "fl_class_threshold": [4],
            "min_fl_run_length": [1, 2],
            "recovery_amplitude_margin": [0.20],
        }
        result = evaluate_grid(data, grid)
        by_run_len = {r.knobs["min_fl_run_length"]: r for r in result.rows}
        assert (
            by_run_len[1].metrics["total_proxy"] >= by_run_len[2].metrics["total_proxy"]
        )


# ---------------------------------------------------------------------------
# Apple target — nightly proxy index vs Apple breathing disturbances
# ---------------------------------------------------------------------------


class TestAppleTarget:
    def test_monotonic_relationship_gives_unit_rho(self):
        nights = [date(2024, 1, d) for d in (1, 2, 3)]
        sessions = [
            _proxy_session(nights[0], n_events=1),
            _proxy_session(nights[1], n_events=2),
            _proxy_session(nights[2], n_events=3),
        ]
        apple_bd = {nights[0]: 1.0, nights[1]: 2.0, nights[2]: 3.0}
        data = SweepData(
            target="apple", proxy_sessions=sessions, apple_bd_by_night=apple_bd
        )
        grid = {
            "fl_class_threshold": [4],
            "min_fl_run_length": [2],
            "recovery_amplitude_margin": [0.20],
        }
        result = evaluate_grid(data, grid)
        row = result.rows[0]
        assert row.metrics["n_paired_nights"] == 3
        assert row.metrics["rho"] == pytest.approx(1.0)
        assert row.is_default

    def test_insufficient_pairs_yields_null_objective(self):
        sessions = [_proxy_session(date(2024, 1, 1), n_events=1)]
        apple_bd = {date(2024, 1, 1): 1.0}
        data = SweepData(
            target="apple", proxy_sessions=sessions, apple_bd_by_night=apple_bd
        )
        result = evaluate_grid(data)
        assert all(r.objective is None for r in result.rows)


# ---------------------------------------------------------------------------
# FLG target
# ---------------------------------------------------------------------------


def _flg_session(mid_insp: list[float], flg: list[float]) -> FlgSessionArrays:
    n = len(mid_insp)
    return FlgSessionArrays(
        session_id=1,
        mid_insp_flattening=np.array(mid_insp, dtype=np.float64),
        flatness_index=np.zeros(n),
        class_weight=np.full(n, np.nan),
        rule_matched=np.zeros(n, dtype=bool),
        breath_flg=np.array(flg, dtype=np.float64),
        session_flg_values=np.array(flg, dtype=np.float64),
    )


class TestFlgTarget:
    def test_perfect_separation_scores_unit_auc(self):
        session = _flg_session([0.9, 0.8, 0.4, 0.3], [0.1, 0.2, 0.6, 0.7])
        data = SweepData(target="flg", flg_sessions=[session])
        grid = {"flg_low_threshold": [0.25], "flg_high_threshold": [0.50]}
        result = evaluate_grid(data, grid)
        row = result.rows[0]
        assert row.metrics["mean_auc_low"] == pytest.approx(1.0)
        assert row.metrics["total_breaths"] == 4
        assert row.is_default

    def test_positive_rate_exposes_degenerate_low_threshold(self):
        session = _flg_session([0.9, 0.8, 0.4, 0.3], [0.1, 0.2, 0.6, 0.7])
        data = SweepData(target="flg", flg_sessions=[session])
        grid = {"flg_low_threshold": [0.05, 0.25], "flg_high_threshold": [0.50]}
        result = evaluate_grid(data, grid)
        by_low = {r.knobs["flg_low_threshold"]: r for r in result.rows}
        # At 0.05 every breath is a positive → degenerate (pos_rate == 1.0).
        assert by_low[0.05].metrics["pos_rate_low"] == pytest.approx(1.0)
        assert by_low[0.25].metrics["pos_rate_low"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Empty data, notice, and export
# ---------------------------------------------------------------------------


class TestEmptyDataHandling:
    @pytest.mark.parametrize("target", ["flg", "re", "apple"])
    def test_empty_cache_produces_null_objectives(self, target: str) -> None:
        result = evaluate_grid(SweepData(target=target))
        assert result.n_units_loaded == 0
        assert len(result.rows) == len(enumerate_grid(DEFAULT_GRIDS[target]))
        assert all(r.objective is None for r in result.rows)

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError, match="unknown sweep target"):
            evaluate_grid(SweepData(target="bogus"))


class TestNoticeEmitted:
    def test_result_carries_not_sweepable_notice(self):
        result = evaluate_grid(SweepData(target="re"))
        assert result.notice == NOT_SWEEPABLE_NOTICE
        assert "re-analysis" in result.notice
        assert "version-bumped" in result.notice


class TestExportSweepCsv:
    def test_exports_full_ranked_grid(self, tmp_path):
        session = _proxy_session(date(2024, 1, 1), n_events=1, machine_starts=[0.0])
        data = SweepData(target="re", proxy_sessions=[session])
        grid = {
            "fl_class_threshold": [4, 6],
            "min_fl_run_length": [2],
            "recovery_amplitude_margin": [0.20],
        }
        result = evaluate_grid(data, grid)
        out = tmp_path / "ranked.csv"
        export_sweep_csv(result, out)

        lines = out.read_text().strip().splitlines()
        header = lines[0].split(",")
        assert "fl_class_threshold" in header
        assert "rank" in header
        assert "objective" in header
        assert "is_default" in header
        assert "sensitivity" in header
        # One data row per grid combination, best-first.
        assert len(lines) - 1 == len(result.rows) == 2
        assert lines[1].startswith("4")  # winning combo exported first
