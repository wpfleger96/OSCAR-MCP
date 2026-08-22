"""
Unit tests for the RERA validation module.

Covers the two pure scoring-core functions (``score_rera_definition`` and
``proxy_reras_from_breath_arrays``, including tunable overrides) and the
validator's skip / scoring paths.  DB interactions use the ``mock_db_session``
fixture from conftest.py plus patched analysis lookups, mirroring the
fl_validator test fixtures.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from snore.analysis.shared.versioning import AnalysisStatus
from snore.analysis.types import AnalysisEvent
from snore.cli.display import fmt_sig
from snore.validation.rera_report import (
    ReraAggregateMetrics,
    ReraSessionValidation,
    ReraValidationReport,
    export_rera_report_csv,
)
from snore.validation.rera_validator import (
    ReraValidator,
    proxy_reras_from_breath_arrays,
    score_rera_definition,
)

_ANALYSIS_BY_ID = "snore.validation.rera_validator.ReraValidator._analysis_by_id"
_LATEST_ANALYSIS = (
    "snore.validation.rera_validator.BreathService.latest_analysis_for_session"
)

# ---------------------------------------------------------------------------
# score_rera_definition (pure)
# ---------------------------------------------------------------------------


class TestScoreReraDefinition:
    def test_perfect_match_gives_unit_scores(self):
        score = score_rera_definition([10.0, 100.0, 200.0], [10.0, 100.0, 200.0])
        assert score.matched == 3
        assert score.sensitivity == 1.0
        assert score.precision == 1.0
        assert score.f1 == 1.0

    def test_within_tolerance_still_matches(self):
        """Starts within EVENT_MATCH_TOLERANCE_SECONDS (5s) match."""
        score = score_rera_definition([12.0], [10.0])
        assert score.matched == 1
        assert score.sensitivity == 1.0

    def test_disjoint_gives_zero_scores(self):
        score = score_rera_definition([10.0, 20.0], [500.0, 600.0])
        assert score.matched == 0
        assert score.sensitivity == 0.0
        assert score.precision == 0.0
        assert score.f1 == 0.0

    def test_no_machine_events_nulls_sensitivity(self):
        score = score_rera_definition([10.0], [])
        assert score.machine_count == 0
        assert score.sensitivity is None
        assert score.f1 is None

    def test_no_programmatic_events_nulls_precision(self):
        score = score_rera_definition([], [10.0, 20.0])
        assert score.programmatic_count == 0
        assert score.sensitivity == 0.0
        assert score.precision is None
        assert score.f1 is None

    def test_partial_overlap(self):
        """2 of 3 machine events matched by 2 programmatic events."""
        score = score_rera_definition([10.0, 20.0], [10.0, 20.0, 30.0])
        assert score.matched == 2
        assert score.sensitivity == pytest.approx(2 / 3)
        assert score.precision == 1.0

    def test_custom_tolerance(self):
        assert score_rera_definition([18.0], [10.0], tolerance=10.0).matched == 1
        assert score_rera_definition([18.0], [10.0], tolerance=5.0).matched == 0

    def test_greedy_one_to_one_match_guard(self):
        """Two proxy events within tolerance of one machine event match it once."""
        score = score_rera_definition([10.0, 12.0], [11.0])
        assert score.matched == 1
        assert score.sensitivity == 1.0  # 1 of 1 machine event matched
        assert score.precision == 0.5  # 1 of 2 programmatic events matched


# ---------------------------------------------------------------------------
# proxy_reras_from_breath_arrays (pure)
# ---------------------------------------------------------------------------


class TestProxyRerasFromBreathArrays:
    def test_run_ending_in_recovery_yields_start_offset(self):
        # Breaths 0,1 are FL (class 5); breath 2 is an explicit recovery.
        starts = proxy_reras_from_breath_arrays(
            flow_class=[5, 5, 1],
            is_recovery_breath=[False, False, True],
            peak_flow_lpm=[20.0, 20.0, 40.0],
            start_offset_s=[100.0, 104.0, 108.0],
        )
        assert starts == [100.0]

    def test_run_without_recovery_yields_nothing(self):
        starts = proxy_reras_from_breath_arrays(
            flow_class=[5, 5, 5],
            is_recovery_breath=[False, False, False],
            peak_flow_lpm=[20.0, 20.0, 20.0],
            start_offset_s=[0.0, 4.0, 8.0],
        )
        assert starts == []

    def test_run_too_short_is_ignored(self):
        # A single FL breath (< MIN_FL_RUN_LENGTH=2) never qualifies.
        starts = proxy_reras_from_breath_arrays(
            flow_class=[5, 1],
            is_recovery_breath=[False, True],
            peak_flow_lpm=[20.0, 40.0],
            start_offset_s=[0.0, 4.0],
        )
        assert starts == []

    def test_amplitude_based_recovery(self):
        # No explicit recovery flag: follower drops to class <=2 with peak flow
        # >= (1 + margin) x run mean (0.20 default -> needs >= 24.0).
        starts = proxy_reras_from_breath_arrays(
            flow_class=[5, 5, 2],
            is_recovery_breath=[None, None, None],
            peak_flow_lpm=[20.0, 20.0, 30.0],
            start_offset_s=[50.0, 54.0, 58.0],
        )
        assert starts == [50.0]

    def test_tunable_threshold_override(self):
        # class 3 runs qualify only when fl_class_threshold is lowered to 3.
        arrays = dict(
            flow_class=[3, 3, 1],
            is_recovery_breath=[False, False, True],
            peak_flow_lpm=[20.0, 20.0, 40.0],
            start_offset_s=[10.0, 14.0, 18.0],
        )
        assert proxy_reras_from_breath_arrays(**arrays) == []
        assert proxy_reras_from_breath_arrays(**arrays, fl_class_threshold=3) == [10.0]

    def test_tunable_min_run_length_override(self):
        arrays = dict(
            flow_class=[5, 1],
            is_recovery_breath=[False, True],
            peak_flow_lpm=[20.0, 40.0],
            start_offset_s=[10.0, 14.0],
        )
        assert proxy_reras_from_breath_arrays(**arrays) == []
        assert proxy_reras_from_breath_arrays(**arrays, min_fl_run_length=1) == [10.0]


# ---------------------------------------------------------------------------
# _select_mode (pure)
# ---------------------------------------------------------------------------


class TestSelectMode:
    @staticmethod
    def _algo(primary_mode: str) -> SimpleNamespace:
        return SimpleNamespace(run=SimpleNamespace(primary_mode=primary_mode))

    def test_prefers_persisted_primary_mode(self):
        # Persisted primary wins over the aasm fallback when available.
        assert (
            ReraValidator._select_mode(self._algo("rules"), ["aasm", "rules"])
            == "rules"
        )

    def test_primary_not_available_falls_back_to_aasm(self):
        assert (
            ReraValidator._select_mode(self._algo("gone"), ["aasm", "rules"]) == "aasm"
        )

    def test_none_algo_falls_back_to_aasm(self):
        assert ReraValidator._select_mode(None, ["aasm", "rules"]) == "aasm"

    def test_no_aasm_uses_first_available(self):
        assert ReraValidator._select_mode(None, ["rules", "other"]) == "rules"


# ---------------------------------------------------------------------------
# Validator paths (mock DB + patched analysis lookups)
# ---------------------------------------------------------------------------


def _session_row(session_id: int = 1, duration_seconds: float = 3600.0) -> MagicMock:
    row = MagicMock()
    row.id = session_id
    row.start_time.strftime = lambda fmt: "2025-06-01"
    row.duration_seconds = duration_seconds
    return row


def _breath(
    flow_class: int | None,
    is_recovery_breath: bool | None,
    peak_flow_lpm: float | None,
    start_offset_s: float,
) -> SimpleNamespace:
    return SimpleNamespace(
        flow_class=flow_class,
        is_recovery_breath=is_recovery_breath,
        peak_flow_lpm=peak_flow_lpm,
        start_offset_s=start_offset_s,
    )


def _machine_re(start_time: float) -> AnalysisEvent:
    return AnalysisEvent(
        event_type="RE", start_time=start_time, duration=5.0, source="machine"
    )


def _analysis(
    amplitude_starts: list[float], machine_starts: list[float]
) -> SimpleNamespace:
    mode_result = SimpleNamespace(
        reras=[SimpleNamespace(start_time=t) for t in amplitude_starts]
    )
    return SimpleNamespace(
        mode_results={"aasm": mode_result},
        machine_events=[_machine_re(t) for t in machine_starts],
    )


def _execute_results(*result_lists: list) -> AsyncMock:
    """Build an execute side_effect yielding scalars().all() / .all() per call."""
    mocks = []
    for spec in result_lists:
        r = MagicMock()
        r.scalars.return_value.all.return_value = spec
        r.all.return_value = spec
        mocks.append(r)
    return AsyncMock(side_effect=mocks)


@pytest.mark.asyncio
async def test_skip_no_analysis(mock_db_session):
    mock_db_session.execute = _execute_results([_session_row(1)])
    with patch(
        _LATEST_ANALYSIS,
        AsyncMock(return_value=(AnalysisStatus.NOT_RUN, None, None)),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    assert report.sessions[0].skipped_reason == "no_analysis"
    assert report.aggregate.sessions_skipped_no_analysis == 1


@pytest.mark.asyncio
async def test_skip_empty_mode_results_is_no_analysis(mock_db_session):
    """OK status but the stored analysis carries no mode_results -> no_analysis."""
    mock_db_session.execute = _execute_results([_session_row(1)])
    empty = SimpleNamespace(mode_results={}, machine_events=[])
    with (
        patch(_LATEST_ANALYSIS, AsyncMock(return_value=(AnalysisStatus.OK, None, 99))),
        patch(_ANALYSIS_BY_ID, AsyncMock(return_value=empty)),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    assert report.sessions[0].skipped_reason == "no_analysis"
    assert report.aggregate.sessions_skipped_no_analysis == 1


@pytest.mark.asyncio
async def test_skip_no_valid_breaths(mock_db_session):
    # execute #1 -> sessions, execute #2 -> empty breaths
    mock_db_session.execute = _execute_results([_session_row(2)], [])
    with (
        patch(_LATEST_ANALYSIS, AsyncMock(return_value=(AnalysisStatus.OK, None, 99))),
        patch(_ANALYSIS_BY_ID, AsyncMock(return_value=_analysis([100.0], [100.0]))),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    assert report.sessions[0].skipped_reason == "no_valid_breaths"
    assert report.aggregate.sessions_skipped_no_valid_breaths == 1


@pytest.mark.asyncio
async def test_no_machine_re_sets_skip_but_reports_counts(mock_db_session):
    breaths = [
        _breath(5, False, 20.0, 100.0),
        _breath(5, False, 20.0, 104.0),
        _breath(1, True, 40.0, 108.0),
    ]
    mock_db_session.execute = _execute_results([_session_row(3)], breaths)
    with (
        patch(_LATEST_ANALYSIS, AsyncMock(return_value=(AnalysisStatus.OK, None, 99))),
        patch(_ANALYSIS_BY_ID, AsyncMock(return_value=_analysis([100.0, 200.0], []))),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    s = report.sessions[0]
    assert s.skipped_reason == "no_machine_re_events"
    assert s.machine_re_count == 0
    assert s.amplitude_rera_count == 2
    assert s.proxy_rera_count == 1
    # Counts/densities still reported; scores nulled with reason.
    assert s.amplitude_density == pytest.approx(2.0)  # 2 events / 1 hour
    assert s.proxy_density == pytest.approx(1.0)
    assert s.amplitude_sensitivity is None
    assert s.amplitude_sensitivity_reason == "no_machine_re_events"
    # Excluded from the scored aggregate but counted.
    agg = report.aggregate
    assert agg.sessions_with_machine_re == 0
    assert agg.sessions_skipped_no_machine_re == 1
    assert agg.total_amplitude_reras == 2
    # No scored sessions -> scored-population metrics are null.
    assert agg.scored_chance_precision_floor is None
    assert agg.pooled_amplitude_precision is None


@pytest.mark.asyncio
async def test_no_programmatic_events_labels_precision(mock_db_session):
    """Amplitude fires zero events on a machine-RE session: precision nulled."""
    breaths = [
        _breath(5, False, 20.0, 100.0),
        _breath(5, False, 20.0, 104.0),
        _breath(1, True, 40.0, 108.0),
    ]
    mock_db_session.execute = _execute_results([_session_row(6)], breaths)
    with (
        patch(_LATEST_ANALYSIS, AsyncMock(return_value=(AnalysisStatus.OK, None, 99))),
        # amplitude produces zero RERAs; machine RE present -> session is scored.
        patch(_ANALYSIS_BY_ID, AsyncMock(return_value=_analysis([], [100.0]))),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    s = report.sessions[0]
    assert s.skipped_reason is None
    assert s.amplitude_rera_count == 0
    assert s.amplitude_matched == 0
    assert s.amplitude_sensitivity == 0.0  # 0 of 1 machine event matched
    assert s.amplitude_precision is None
    assert s.amplitude_precision_reason == "no_programmatic_events"
    assert s.amplitude_f1 is None
    assert s.amplitude_f1_reason == "no_programmatic_events"


@pytest.mark.asyncio
async def test_zero_duration_nulls_densities(mock_db_session):
    """A scored session with zero duration reports densities as null-with-reason."""
    breaths = [
        _breath(5, False, 20.0, 100.0),
        _breath(5, False, 20.0, 104.0),
        _breath(1, True, 40.0, 108.0),
    ]
    mock_db_session.execute = _execute_results(
        [_session_row(7, duration_seconds=0)], breaths
    )
    with (
        patch(_LATEST_ANALYSIS, AsyncMock(return_value=(AnalysisStatus.OK, None, 99))),
        patch(_ANALYSIS_BY_ID, AsyncMock(return_value=_analysis([100.0], [100.0]))),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    s = report.sessions[0]
    assert s.skipped_reason is None  # still scored
    assert s.machine_re_count == 1
    assert s.machine_re_density is None
    assert s.machine_re_density_reason == "zero_duration"
    assert s.amplitude_density is None
    assert s.amplitude_density_reason == "zero_duration"
    assert s.proxy_density is None


@pytest.mark.asyncio
async def test_fully_scored_session(mock_db_session):
    breaths = [
        _breath(5, False, 20.0, 100.0),
        _breath(5, False, 20.0, 104.0),
        _breath(1, True, 40.0, 108.0),
    ]
    mock_db_session.execute = _execute_results([_session_row(4)], breaths)
    with (
        patch(_LATEST_ANALYSIS, AsyncMock(return_value=(AnalysisStatus.OK, None, 99))),
        patch(_ANALYSIS_BY_ID, AsyncMock(return_value=_analysis([100.0], [100.0]))),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    s = report.sessions[0]
    assert s.skipped_reason is None
    assert s.machine_re_count == 1
    # Amplitude RERA at 100.0 matches machine RE at 100.0.
    assert s.amplitude_matched == 1
    assert s.amplitude_sensitivity == 1.0
    assert s.amplitude_precision == 1.0
    assert s.amplitude_f1 == 1.0
    # Proxy run starts at 100.0 -> also matches.
    assert s.proxy_matched == 1
    assert s.proxy_sensitivity == 1.0
    assert s.proxy_precision == 1.0

    agg = report.aggregate
    assert agg.sessions_with_machine_re == 1
    assert agg.total_machine_re == 1
    assert agg.match_tolerance_seconds == 5.0
    # Chance floor = (1 RE / 3600 s) * 2 * 5 s (one scored session, 1 h).
    assert agg.chance_precision_floor == pytest.approx((1 / 3600.0) * 10.0)
    assert agg.scored_chance_precision_floor == pytest.approx((1 / 3600.0) * 10.0)
    assert agg.mean_amplitude_precision == 1.0
    assert agg.pooled_amplitude_precision == 1.0
    assert agg.pooled_proxy_sensitivity == 1.0


@pytest.mark.asyncio
async def test_error_session_marked_error(mock_db_session):
    mock_db_session.execute = _execute_results([_session_row(5)])
    with patch(_LATEST_ANALYSIS, AsyncMock(side_effect=RuntimeError("boom"))):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    assert report.sessions[0].skipped_reason == "error"
    assert report.aggregate.sessions_skipped_error == 1


# ---------------------------------------------------------------------------
# Aggregate — denominator population and scored-population metrics
# ---------------------------------------------------------------------------


def _scored(
    *,
    session_id: int,
    hours: float,
    machine_re: int,
    amplitude: int,
    proxy: int,
    amplitude_matched: int,
    proxy_matched: int,
) -> ReraSessionValidation:
    return ReraSessionValidation(
        session_id=session_id,
        date="2025-06-01",
        duration_hours=hours,
        skipped_reason=None,
        machine_re_count=machine_re,
        amplitude_rera_count=amplitude,
        proxy_rera_count=proxy,
        amplitude_matched=amplitude_matched,
        proxy_matched=proxy_matched,
    )


def _skipped(
    *, session_id: int, hours: float, reason: str, amplitude: int = 0, proxy: int = 0
) -> ReraSessionValidation:
    return ReraSessionValidation(
        session_id=session_id,
        date="2025-06-01",
        duration_hours=hours,
        skipped_reason=reason,
        amplitude_rera_count=amplitude,
        proxy_rera_count=proxy,
    )


class TestAggregateDenominator:
    def _mixed_population(self) -> list[ReraSessionValidation]:
        # Denominator must include only the scored (8 h) and no-machine-RE (6 h)
        # sessions = 14 h; the no-analysis (7 h) and error (5 h) sessions carry
        # structurally-zero counts and must not dilute the pooled densities.
        return [
            _scored(
                session_id=1,
                hours=8.0,
                machine_re=2,
                amplitude=4,
                proxy=3,
                amplitude_matched=1,
                proxy_matched=1,
            ),
            _skipped(
                session_id=2,
                hours=6.0,
                reason="no_machine_re_events",
                amplitude=5,
                proxy=2,
            ),
            _skipped(session_id=3, hours=7.0, reason="no_analysis"),
            _skipped(session_id=4, hours=5.0, reason="error"),
        ]

    def test_pooled_densities_use_only_evaluated_hours(self):
        agg = ReraValidator._calculate_aggregate(self._mixed_population())
        # Denominator is 14 h, NOT 26 h (excludes no-analysis + error).
        assert agg.machine_re_density == pytest.approx(2 / 14)
        assert agg.amplitude_density == pytest.approx((4 + 5) / 14)
        assert agg.proxy_density == pytest.approx((3 + 2) / 14)

    def test_whole_dataset_floor_uses_evaluated_hours(self):
        agg = ReraValidator._calculate_aggregate(self._mixed_population())
        assert agg.chance_precision_floor == pytest.approx((2 / (14 * 3600.0)) * 10.0)

    def test_scored_floor_uses_scored_hours_only(self):
        agg = ReraValidator._calculate_aggregate(self._mixed_population())
        # Scored floor divides by 8 h, so it is strictly higher than the
        # whole-dataset floor's 14 h denominator.
        assert agg.scored_chance_precision_floor == pytest.approx(
            (2 / (8 * 3600.0)) * 10.0
        )
        assert agg.scored_chance_precision_floor > agg.chance_precision_floor

    def test_pooled_scores_use_scored_totals(self):
        agg = ReraValidator._calculate_aggregate(self._mixed_population())
        # matched 1 / amplitude 4; matched 1 / machine RE 2.
        assert agg.pooled_amplitude_precision == pytest.approx(1 / 4)
        assert agg.pooled_amplitude_sensitivity == pytest.approx(1 / 2)
        assert agg.pooled_proxy_precision == pytest.approx(1 / 3)
        assert agg.pooled_proxy_sensitivity == pytest.approx(1 / 2)


# ---------------------------------------------------------------------------
# Display / export precision (tiny magnitudes must stay visible / lossless)
# ---------------------------------------------------------------------------


class TestFmtSig:
    def test_tiny_value_uses_scientific_notation(self):
        # The chance floor (~4e-5) collapses to 0.000 at fixed 3 decimals.
        rendered = fmt_sig(4.06e-5)
        assert "e-" in rendered
        assert rendered != "0.000"

    def test_sub_tenth_keeps_four_decimals(self):
        assert fmt_sig(0.001234) == "0.0012"

    def test_normal_value_three_decimals(self):
        assert fmt_sig(0.5) == "0.500"

    def test_zero_and_none(self):
        assert fmt_sig(0.0) == "0"
        assert fmt_sig(None) == "N/A"
        assert fmt_sig(None, na=" N/A") == " N/A"


def test_csv_export_preserves_full_precision(tmp_path):
    """CSV export must not round tiny metrics (proxy precision ~1e-3) away."""
    tiny = 0.0012345678901234
    session = ReraSessionValidation(
        session_id=1,
        date="2025-06-01",
        duration_hours=8.0,
        machine_re_count=2,
        proxy_rera_count=1,
        proxy_matched=1,
        proxy_precision=tiny,
    )
    agg = ReraAggregateMetrics(
        total_sessions=1,
        sessions_with_machine_re=1,
        sessions_skipped_no_machine_re=0,
        sessions_skipped_no_analysis=0,
        sessions_skipped_no_valid_breaths=0,
        sessions_skipped_error=0,
        total_machine_re=2,
        total_amplitude_reras=0,
        total_proxy_reras=1,
        match_tolerance_seconds=5.0,
    )
    report = ReraValidationReport(
        report_date="2025-06-02 00:00:00",
        date_range_start="2025-06-01",
        date_range_end="2025-06-01",
        aggregate=agg,
        sessions=[session],
    )
    out = tmp_path / "rera.csv"
    export_rera_report_csv(report, out)
    text = out.read_text()
    assert repr(tiny) in text  # exact round-trippable value, not "0.0012"
    assert "amplitude_matched" in text  # matched columns are exported
    assert "proxy_matched" in text
