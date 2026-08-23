"""Unit tests for the offline threshold-sweep harness.

The engine is exercised through its pure seams — ``score_fl_arrays`` (the
extraction regression), ``enumerate_grid``, ``evaluate_grid``, and
``export_sweep_csv`` — driven over hand-built in-memory caches with no DB.  A
planted-signal case proves the correct knob setting ranks first.
"""

from __future__ import annotations

import csv

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from snore.constants import FlowLimitationConstants
from snore.services.analysis_facade import AnalysisFacade
from snore.services.health_service import HealthService
from snore.validation.fl_validator import auc_severity_vs_flg, score_fl_arrays
from snore.validation.rera_validator import (
    build_proxy_breath_rows,
    proxy_reras_from_breath_arrays,
)
from snore.validation.sweep import (
    DEFAULT_GRIDS,
    DEFAULT_KNOBS,
    NOT_SWEEPABLE_NOTICE,
    FlgSessionArrays,
    ProxySessionArrays,
    SweepData,
    _is_default,
    _is_fallback_flow_confidence,
    _proxy_starts,
    enumerate_grid,
    evaluate_grid,
    export_sweep_csv,
    load_sweep_data,
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
    # These breaths are all rule-matched (high confidence), so the fallback-masked
    # row list is identical — include_fallback has no effect on this fixture.
    rows = build_proxy_breath_rows(flow_class, is_recovery, peak)
    return ProxySessionArrays(
        session_id=int(therapy_date.strftime("%Y%m%d")),
        therapy_date=therapy_date,
        duration_hours=duration_hours,
        proxy_rows=rows,
        proxy_rows_no_fallback=rows,
        start_offset_s=start_offset,
        machine_starts=machine_starts if machine_starts is not None else [],
    )


# ---------------------------------------------------------------------------
# Proxy caching — the cached-row path must equal the production-parity seam
# ---------------------------------------------------------------------------


class TestProxyCachedRowEquivalence:
    def test_cached_rows_match_seam_function(self):
        # Two FL runs: the first ends in an explicit recovery breath, the second
        # in an amplitude-margin recovery — exercising both recovery branches.
        flow_class = [4, 5, 1, 2, 6, 6, 1, 3]
        is_recovery = [False, False, True, False, False, False, False, False]
        peak = [20.0, 22.0, 40.0, 10.0, 18.0, 19.0, 30.0, 5.0]
        start_offset = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]
        rows = build_proxy_breath_rows(flow_class, is_recovery, peak)
        session = ProxySessionArrays(
            session_id=1,
            therapy_date=date(2024, 1, 1),
            duration_hours=8.0,
            proxy_rows=rows,
            proxy_rows_no_fallback=rows,
            start_offset_s=start_offset,
            machine_starts=[],
        )
        for knobs in (
            {
                "fl_class_threshold": 4,
                "min_fl_run_length": 2,
                "recovery_amplitude_margin": 0.20,
            },
            {
                "fl_class_threshold": 5,
                "min_fl_run_length": 1,
                "recovery_amplitude_margin": 0.30,
            },
            {
                "fl_class_threshold": 4,
                "min_fl_run_length": 1,
                "recovery_amplitude_margin": 0.10,
            },
        ):
            cached = _proxy_starts(session, knobs)
            seam = proxy_reras_from_breath_arrays(
                flow_class,
                is_recovery,
                peak,
                start_offset,
                fl_class_threshold=int(knobs["fl_class_threshold"]),
                min_fl_run_length=int(knobs["min_fl_run_length"]),
                recovery_amplitude_margin=float(knobs["recovery_amplitude_margin"]),
            )
            assert cached == seam


# ---------------------------------------------------------------------------
# include_fallback knob — excluding low-confidence fallback guesses offline
# ---------------------------------------------------------------------------


def _proxy_session_with_confidence(
    flow_class: list[int | None],
    is_recovery: list[bool | None],
    peak: list[float | None],
    flow_confidence: list[float | None],
    start_offset: list[float],
) -> ProxySessionArrays:
    """Build a session with both row lists, masking fallback-confidence breaths.

    Mirrors the loader: a breath is a fallback guess when its confidence is None
    or ``<= FL_DEFAULT_CONFIDENCE``; its ``flow_class`` is nulled in the masked
    list so the ``include_fallback=0.0`` path drops it from FL-run detection.
    """
    masked = [
        None if _is_fallback_flow_confidence(c) else fc
        for fc, c in zip(flow_class, flow_confidence, strict=True)
    ]
    return ProxySessionArrays(
        session_id=1,
        therapy_date=date(2024, 1, 1),
        duration_hours=8.0,
        proxy_rows=build_proxy_breath_rows(flow_class, is_recovery, peak),
        proxy_rows_no_fallback=build_proxy_breath_rows(masked, is_recovery, peak),
        start_offset_s=start_offset,
        machine_starts=[],
    )


# A length-3 FL run whose middle breath is a fallback guess (confidence 0.5),
# followed by an explicit recovery breath.  Included: one event; excluded: the
# fallback breath splits the run into two sub-runs of length 1, so it no longer
# meets min_fl_run_length=2 and the event vanishes.
_MIXED_FLOW_CLASS = [4, 4, 4, 1]
_MIXED_CONFIDENCE = [0.9, 0.5, 0.9, 0.9]
_MIXED_IS_RECOVERY = [False, False, False, True]
_MIXED_PEAK = [20.0, 20.0, 20.0, 40.0]
_MIXED_START = [0.0, 2.0, 4.0, 6.0]

_KNOBS_BASE = {
    "fl_class_threshold": 4,
    "min_fl_run_length": 2,
    "recovery_amplitude_margin": 0.20,
}


class TestIncludeFallbackKnob:
    def _mixed_session(self) -> ProxySessionArrays:
        return _proxy_session_with_confidence(
            _MIXED_FLOW_CLASS,
            _MIXED_IS_RECOVERY,
            _MIXED_PEAK,
            _MIXED_CONFIDENCE,
            _MIXED_START,
        )

    def test_include_fallback_on_matches_all_breaths(self):
        session = self._mixed_session()
        starts = _proxy_starts(session, {**_KNOBS_BASE, "include_fallback": 1.0})
        # Identical to the pre-change seam driven over every breath.
        assert starts == proxy_reras_from_breath_arrays(
            _MIXED_FLOW_CLASS,
            _MIXED_IS_RECOVERY,
            _MIXED_PEAK,
            _MIXED_START,
            fl_class_threshold=4,
            min_fl_run_length=2,
            recovery_amplitude_margin=0.20,
        )
        assert starts == [0.0]

    def test_absent_knob_defaults_to_including_fallback(self):
        # A grid that never sweeps the knob reproduces current behaviour exactly.
        session = self._mixed_session()
        assert _proxy_starts(session, _KNOBS_BASE) == [0.0]

    def test_include_fallback_off_drops_split_run(self):
        session = self._mixed_session()
        assert _proxy_starts(session, {**_KNOBS_BASE, "include_fallback": 0.0}) == []

    def test_grid_proxy_count_differs_across_knob_values(self):
        data = SweepData(
            target="apple",
            proxy_sessions=[self._mixed_session()],
            apple_bd_by_night={date(2024, 1, 1): 1.0},
        )
        grid = {
            "fl_class_threshold": [4],
            "min_fl_run_length": [2],
            "recovery_amplitude_margin": [0.20],
            "include_fallback": [1.0, 0.0],
        }
        result = evaluate_grid(data, grid)
        by_knob = {r.knobs["include_fallback"]: r for r in result.rows}
        assert by_knob[1.0].metrics["total_proxy"] == 1
        assert by_knob[0.0].metrics["total_proxy"] == 0

    def test_default_combo_detected_with_new_knob(self):
        data = SweepData(
            target="re",
            proxy_sessions=[self._mixed_session()],
        )
        grid = {
            "fl_class_threshold": [4],
            "min_fl_run_length": [2],
            "recovery_amplitude_margin": [0.20],
            "include_fallback": [1.0, 0.0],
        }
        result = evaluate_grid(data, grid)
        by_knob = {r.knobs["include_fallback"]: r for r in result.rows}
        assert by_knob[1.0].is_default
        assert not by_knob[0.0].is_default

    def test_is_default_treats_include_fallback_on_as_default(self):
        base = {
            "fl_class_threshold": 4,
            "min_fl_run_length": 2,
            "recovery_amplitude_margin": 0.20,
        }
        assert _is_default({**base, "include_fallback": 1.0}, "re")
        assert not _is_default({**base, "include_fallback": 0.0}, "re")
        # An unswept axis is held at its production default (fallback included).
        assert _is_default(base, "re")

    def test_fallback_confidence_classification(self):
        floor = FlowLimitationConstants.FL_DEFAULT_CONFIDENCE
        assert _is_fallback_flow_confidence(None)
        assert _is_fallback_flow_confidence(floor)
        assert not _is_fallback_flow_confidence(floor + 0.1)

    def test_re_and_apple_default_grids_pin_fallback_included(self):
        for target in ("re", "apple"):
            assert DEFAULT_KNOBS[target]["include_fallback"] == 1.0
            assert DEFAULT_GRIDS[target]["include_fallback"] == [1.0]
        assert "include_fallback" not in DEFAULT_KNOBS["flg"]


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
    mid = np.array(mid_insp, dtype=np.float64)
    flg_arr = np.array(flg, dtype=np.float64)
    valid = ~np.isnan(flg_arr)
    return FlgSessionArrays(
        session_id=1,
        flattening_severity_valid=(1.0 - mid)[valid],
        flg_valid=flg_arr[valid],
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

    def test_grid_auc_matches_score_fl_arrays_seam(self):
        # Pin: the FLG grid's cached-invariant AUCs equal what score_fl_arrays
        # computes for the same arrays and breakpoints (extraction is exact).
        # A NaN-FLG breath exercises the shared valid mask.
        mid_insp = np.array([0.9, 0.8, 0.5, 0.3, 0.2], dtype=np.float64)
        flatness = np.zeros(5, dtype=np.float64)
        class_weight = np.full(5, np.nan, dtype=np.float64)
        rule_matched = np.zeros(5, dtype=bool)
        breath_flg = np.array([0.1, np.nan, 0.4, 0.6, 0.8], dtype=np.float64)
        session_flg = breath_flg[~np.isnan(breath_flg)]
        valid = ~np.isnan(breath_flg)
        sev_valid = (1.0 - mid_insp)[valid]
        flg_valid = breath_flg[valid]
        for low, high in [(0.25, 0.50), (0.15, 0.35), (0.35, 0.60)]:
            scores = score_fl_arrays(
                mid_insp,
                flatness,
                class_weight,
                rule_matched,
                breath_flg,
                session_flg,
                flg_low_threshold=low,
                flg_high_threshold=high,
            )
            assert scores is not None
            assert auc_severity_vs_flg(sev_valid, flg_valid, low) == scores.auc_low
            assert auc_severity_vs_flg(sev_valid, flg_valid, high) == scores.auc_high


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

    def test_objective_written_at_full_precision(self, tmp_path):
        # The objective (sensitivity - tiny chance floor) has many significant
        # digits; repr formatting must preserve them, not truncate to 6 places.
        session = _proxy_session(date(2024, 1, 1), n_events=1, machine_starts=[0.0])
        data = SweepData(target="re", proxy_sessions=[session])
        grid = {
            "fl_class_threshold": [4],
            "min_fl_run_length": [2],
            "recovery_amplitude_margin": [0.20],
        }
        result = evaluate_grid(data, grid)
        out = tmp_path / "ranked.csv"
        export_sweep_csv(result, out)

        rows = list(csv.DictReader(out.read_text().splitlines()))
        assert result.rows[0].objective is not None
        # The cell round-trips back to the exact float — full precision preserved.
        assert float(rows[0]["objective"]) == result.rows[0].objective
        assert rows[0]["objective"] == repr(result.rows[0].objective)


# ---------------------------------------------------------------------------
# Load-once loaders (mock DB) — the only DB-touching code in the harness
# ---------------------------------------------------------------------------


def _algo_versions_json() -> dict:
    from snore.analysis.shared.versioning import (
        AlgorithmIdentity,
        AlgoVersions,
        AnalysisRunMetadata,
    )

    return AlgoVersions(
        identity=AlgorithmIdentity.current(),
        run=AnalysisRunMetadata(primary_mode="aasm", modes=["aasm"]),
    ).model_dump()


def _analysis_row(ar_id: int = 99) -> MagicMock:
    """AnalysisResult mock BreathService classifies as OK (current identity)."""
    row = MagicMock()
    row.engine_versions_json = _algo_versions_json()
    row.id = ar_id
    return row


def _session_row(session_id: int = 1, duration_seconds: float = 28800.0) -> MagicMock:
    row = MagicMock()
    row.id = session_id
    row.start_time = datetime(2025, 1, 1, 22, 0, 0)
    row.duration_seconds = duration_seconds
    return row


def _waveform_blob(ts: list[float], vs: list[float]) -> bytes:
    return (
        np.column_stack([np.array(ts, np.float32), np.array(vs, np.float32)])
        .astype(np.float32)
        .tobytes()
    )


def _flg_breath(start: float, end: float, mid_insp: float) -> MagicMock:
    b = MagicMock()
    b.start_offset_s = start
    b.end_offset_s = end
    b.mid_insp_flattening = mid_insp
    return b


def _proxy_breath_row(
    flow_class: int | None,
    is_recovery: bool | None,
    peak: float | None,
    start: float,
    flow_confidence: float | None = 0.9,
) -> MagicMock:
    b = MagicMock()
    b.flow_class = flow_class
    b.is_recovery_breath = is_recovery
    b.peak_flow_lpm = peak
    b.start_offset_s = start
    b.flow_confidence = flow_confidence
    return b


def _result_scalars_all(rows: list) -> MagicMock:
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    return res


def _result_scalars_first(row: object) -> MagicMock:
    res = MagicMock()
    res.scalars.return_value.first.return_value = row
    return res


def _result_all(rows: list) -> MagicMock:
    res = MagicMock()
    res.all.return_value = rows
    return res


class TestLoadSweepData:
    """Drive the DB loaders through the public load_sweep_data entry point."""

    @pytest.mark.asyncio
    async def test_flg_assembles_valid_arrays(self, mock_db_session):
        # Two breath windows over a covering FLG waveform.
        ts = [0.0, 2.0, 4.0, 6.0]
        vs = [0.1, 0.3, 0.5, 0.7]
        waveform = MagicMock()
        waveform.data_blob = _waveform_blob(ts, vs)
        waveform.sample_count = 4
        breaths = [_flg_breath(0.0, 4.0, 0.9), _flg_breath(4.0, 8.0, 0.5)]
        mock_db_session.execute = AsyncMock(
            side_effect=[
                _result_scalars_all([_session_row(1)]),
                _result_scalars_first(waveform),
                _result_scalars_first(_analysis_row(99)),
                _result_scalars_all(breaths),
            ]
        )
        data = await load_sweep_data(
            mock_db_session, 1, "2025-01-01", "2025-01-31", "flg"
        )
        assert len(data.flg_sessions) == 1
        s = data.flg_sessions[0]
        assert s.session_id == 1
        # Breath 0 [0,4): mean(0.1,0.3)=0.2; breath 1 [4,8): mean(0.5,0.7)=0.6.
        np.testing.assert_allclose(s.flg_valid, [0.2, 0.6], atol=1e-6)
        # severity = 1 - mid_insp = [0.1, 0.5].
        np.testing.assert_allclose(s.flattening_severity_valid, [0.1, 0.5], atol=1e-6)

    @pytest.mark.asyncio
    async def test_flg_silently_drops_unscoreable_session(self, mock_db_session):
        # Session 1 scoreable; session 2 has no FLG waveform → walrus-dropped.
        ts = [0.0, 2.0, 4.0, 6.0]
        vs = [0.1, 0.3, 0.5, 0.7]
        waveform = MagicMock()
        waveform.data_blob = _waveform_blob(ts, vs)
        waveform.sample_count = 4
        mock_db_session.execute = AsyncMock(
            side_effect=[
                _result_scalars_all([_session_row(1), _session_row(2)]),
                _result_scalars_first(waveform),
                _result_scalars_first(_analysis_row(99)),
                _result_scalars_all([_flg_breath(0.0, 4.0, 0.8)]),
                _result_scalars_first(None),  # session 2: no waveform row
            ]
        )
        data = await load_sweep_data(
            mock_db_session, 1, "2025-01-01", "2025-01-31", "flg"
        )
        assert [s.session_id for s in data.flg_sessions] == [1]

    @pytest.mark.asyncio
    async def test_apple_assembles_rows_and_loads_breathing_disturbance(
        self, mock_db_session
    ):
        rows = [
            _proxy_breath_row(4, False, 20.0, 0.0),
            _proxy_breath_row(4, False, 20.0, 2.0),
            _proxy_breath_row(1, True, 40.0, 4.0),
        ]
        mock_db_session.execute = AsyncMock(
            side_effect=[
                _result_scalars_all([_session_row(1, duration_seconds=28800.0)]),
                _result_scalars_first(_analysis_row(99)),
                _result_all(rows),
            ]
        )
        with patch.object(
            HealthService,
            "get_breathing_disturbance_by_night",
            AsyncMock(return_value={date(2025, 1, 1): 3.0}),
        ):
            data = await load_sweep_data(
                mock_db_session, 1, "2025-01-01", "2025-01-31", "apple"
            )
        assert len(data.proxy_sessions) == 1
        s = data.proxy_sessions[0]
        assert [b.flow_class for b in s.proxy_rows] == [4, 4, 1]
        assert s.start_offset_s == [0.0, 2.0, 4.0]
        assert s.duration_hours == pytest.approx(8.0)
        assert s.machine_starts == []  # apple target loads no machine RE
        assert data.apple_bd_by_night == {date(2025, 1, 1): 3.0}

    @pytest.mark.asyncio
    async def test_loader_masks_fallback_flow_class(self, mock_db_session):
        # Middle breath is a fallback guess (confidence 0.5); the loader must
        # null its flow_class in the fallback-masked row list only.
        rows = [
            _proxy_breath_row(4, False, 20.0, 0.0, flow_confidence=0.9),
            _proxy_breath_row(4, False, 20.0, 2.0, flow_confidence=0.5),
            _proxy_breath_row(1, True, 40.0, 4.0, flow_confidence=0.9),
        ]
        mock_db_session.execute = AsyncMock(
            side_effect=[
                _result_scalars_all([_session_row(1)]),
                _result_scalars_first(_analysis_row(99)),
                _result_all(rows),
            ]
        )
        with patch.object(
            HealthService,
            "get_breathing_disturbance_by_night",
            AsyncMock(return_value={}),
        ):
            data = await load_sweep_data(
                mock_db_session, 1, "2025-01-01", "2025-01-31", "apple"
            )
        s = data.proxy_sessions[0]
        assert [b.flow_class for b in s.proxy_rows] == [4, 4, 1]
        assert [b.flow_class for b in s.proxy_rows_no_fallback] == [4, None, 1]

    @pytest.mark.asyncio
    async def test_re_target_extracts_machine_starts(self, mock_db_session):
        rows = [_proxy_breath_row(4, False, 20.0, 0.0)]
        mock_db_session.execute = AsyncMock(
            side_effect=[
                _result_scalars_all([_session_row(1)]),
                _result_scalars_first(_analysis_row(99)),
                _result_all(rows),
            ]
        )
        with (
            patch.object(
                AnalysisFacade,
                "get_analysis_result",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "snore.validation.sweep.convert_machine_reras",
                return_value=[MagicMock(start_time=12.5), MagicMock(start_time=30.0)],
            ),
        ):
            data = await load_sweep_data(
                mock_db_session, 1, "2025-01-01", "2025-01-31", "re"
            )
        assert len(data.proxy_sessions) == 1
        assert data.proxy_sessions[0].machine_starts == [12.5, 30.0]
        assert data.apple_bd_by_night == {}  # RE target loads no Apple data

    @pytest.mark.asyncio
    async def test_unknown_target_raises(self, mock_db_session):
        with pytest.raises(ValueError, match="unknown sweep target"):
            await load_sweep_data(
                mock_db_session, 1, "2025-01-01", "2025-01-31", "bogus"
            )
