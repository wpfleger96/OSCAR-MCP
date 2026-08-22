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
from snore.validation.rera_validator import (
    ReraValidator,
    proxy_reras_from_breath_arrays,
    score_rera_definition,
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
        flow_confidence=0.9,
        is_recovery_breath=is_recovery_breath,
        peak_flow_lpm=peak_flow_lpm,
        start_offset_s=start_offset_s,
        end_offset_s=start_offset_s + 4.0,
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
        "snore.validation.rera_validator.BreathService.latest_analysis_for_session",
        AsyncMock(return_value=(AnalysisStatus.NOT_RUN, None, None)),
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
        patch(
            "snore.validation.rera_validator.BreathService.latest_analysis_for_session",
            AsyncMock(return_value=(AnalysisStatus.OK, None, 99)),
        ),
        patch(
            "snore.services.analysis_facade.AnalysisFacade.get_analysis_result",
            AsyncMock(return_value=_analysis([100.0], [100.0])),
        ),
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
        patch(
            "snore.validation.rera_validator.BreathService.latest_analysis_for_session",
            AsyncMock(return_value=(AnalysisStatus.OK, None, 99)),
        ),
        patch(
            "snore.services.analysis_facade.AnalysisFacade.get_analysis_result",
            AsyncMock(return_value=_analysis([100.0, 200.0], [])),
        ),
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


@pytest.mark.asyncio
async def test_fully_scored_session(mock_db_session):
    breaths = [
        _breath(5, False, 20.0, 100.0),
        _breath(5, False, 20.0, 104.0),
        _breath(1, True, 40.0, 108.0),
    ]
    mock_db_session.execute = _execute_results([_session_row(4)], breaths)
    with (
        patch(
            "snore.validation.rera_validator.BreathService.latest_analysis_for_session",
            AsyncMock(return_value=(AnalysisStatus.OK, None, 99)),
        ),
        patch(
            "snore.services.analysis_facade.AnalysisFacade.get_analysis_result",
            AsyncMock(return_value=_analysis([100.0], [100.0])),
        ),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    s = report.sessions[0]
    assert s.skipped_reason is None
    assert s.machine_re_count == 1
    # Amplitude RERA at 100.0 matches machine RE at 100.0.
    assert s.amplitude_sensitivity == 1.0
    assert s.amplitude_precision == 1.0
    assert s.amplitude_f1 == 1.0
    # Proxy run starts at 100.0 -> also matches.
    assert s.proxy_sensitivity == 1.0
    assert s.proxy_precision == 1.0

    agg = report.aggregate
    assert agg.sessions_with_machine_re == 1
    assert agg.total_machine_re == 1
    assert agg.match_tolerance_seconds == 5.0
    # Chance floor = (1 RE / 3600 s) * 2 * 5 s.
    assert agg.chance_precision_floor == pytest.approx((1 / 3600.0) * 10.0)
    assert agg.mean_amplitude_precision == 1.0


@pytest.mark.asyncio
async def test_error_session_marked_error(mock_db_session):
    mock_db_session.execute = _execute_results([_session_row(5)])
    with patch(
        "snore.validation.rera_validator.BreathService.latest_analysis_for_session",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        report = await ReraValidator(mock_db_session, 1).validate_date_range(
            "2025-06-01", "2025-06-30"
        )
    assert report.sessions[0].skipped_reason == "error"
    assert report.aggregate.sessions_skipped_error == 1
