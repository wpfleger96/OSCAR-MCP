"""
Unit tests for the breath-trends validation module.

Tests metric computation, channel skip paths, session skip paths, zero-device-average
masking, I:E computation guard, and Spearman edge cases.
All DB interactions use the mock_db_session fixture from conftest.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from snore.validation.breath_trends_validator import (
    BreathTrendsValidator,
    _compute_channel_metrics,
    _snore_ie_ratio,
    _snore_rr,
    _snore_ti,
    _snore_tv,
)

# ---------------------------------------------------------------------------
# Per-breath SNORE value computation helpers
# ---------------------------------------------------------------------------


def _make_breath(
    start: float = 0.0,
    end: float = 3.0,
    tv_ml: float | None = 500.0,
    ti_s: float | None = 1.0,
    te_s: float | None = 2.0,
) -> MagicMock:
    b = MagicMock()
    b.start_offset_s = start
    b.end_offset_s = end
    b.tidal_volume_ml = tv_ml
    b.inspiration_time_s = ti_s
    b.expiration_time_s = te_s
    return b


class TestSnoreRr:
    def test_basic_rr(self):
        """RR = 60 / duration."""
        b = _make_breath(start=0.0, end=3.0)
        vals = _snore_rr([b])
        np.testing.assert_allclose(vals[0], 60.0 / 3.0)

    def test_zero_duration_is_nan(self):
        b = _make_breath(start=5.0, end=5.0)
        vals = _snore_rr([b])
        assert np.isnan(vals[0])

    def test_negative_duration_is_nan(self):
        b = _make_breath(start=5.0, end=3.0)
        vals = _snore_rr([b])
        assert np.isnan(vals[0])

    def test_multiple_breaths(self):
        breaths = [
            _make_breath(0.0, 2.0),
            _make_breath(2.0, 4.0),
            _make_breath(4.0, 5.0),
        ]
        vals = _snore_rr(breaths)
        assert vals.shape == (3,)
        np.testing.assert_allclose(vals[0], 30.0)
        np.testing.assert_allclose(vals[1], 30.0)
        np.testing.assert_allclose(vals[2], 60.0)


class TestSnoreTv:
    def test_returns_tidal_volume_ml(self):
        b = _make_breath(tv_ml=450.0)
        vals = _snore_tv([b])
        np.testing.assert_allclose(vals[0], 450.0)

    def test_none_becomes_nan(self):
        b = _make_breath(tv_ml=None)
        vals = _snore_tv([b])
        assert np.isnan(vals[0])


class TestSnoreTi:
    def test_returns_inspiration_time(self):
        b = _make_breath(ti_s=1.2)
        vals = _snore_ti([b])
        np.testing.assert_allclose(vals[0], 1.2)

    def test_none_becomes_nan(self):
        b = _make_breath(ti_s=None)
        vals = _snore_ti([b])
        assert np.isnan(vals[0])


class TestSnoreIeRatio:
    def test_basic_ie_ratio(self):
        """100 * Ti / Te: Ti=1, Te=2 → 50."""
        b = _make_breath(ti_s=1.0, te_s=2.0)
        vals = _snore_ie_ratio([b])
        np.testing.assert_allclose(vals[0], 50.0)

    def test_te_zero_is_nan(self):
        """Guard against division by zero."""
        b = _make_breath(ti_s=1.0, te_s=0.0)
        vals = _snore_ie_ratio([b])
        assert np.isnan(vals[0])

    def test_ti_none_is_nan(self):
        b = _make_breath(ti_s=None, te_s=2.0)
        vals = _snore_ie_ratio([b])
        assert np.isnan(vals[0])

    def test_te_none_is_nan(self):
        b = _make_breath(ti_s=1.0, te_s=None)
        vals = _snore_ie_ratio([b])
        assert np.isnan(vals[0])

    def test_typical_bilevel_ratio(self):
        """Ti=0.9, Te=2.1 → 100 × 0.9/2.1 ≈ 42.857."""
        b = _make_breath(ti_s=0.9, te_s=2.1)
        vals = _snore_ie_ratio([b])
        np.testing.assert_allclose(vals[0], 100.0 * 0.9 / 2.1, rtol=1e-5)


# ---------------------------------------------------------------------------
# _compute_channel_metrics — metric math and masking
# ---------------------------------------------------------------------------


class TestComputeChannelMetrics:
    def test_known_mae_and_bias(self):
        """Exact MAE and bias with synthetic pairs."""
        snore = np.array([10.0, 12.0, 8.0, 11.0])
        device = np.array([9.0, 11.0, 9.0, 10.0])
        # diff = [1, 1, -1, 1], abs_diff = [1, 1, 1, 1] → MAE = 1.0, bias = 0.5
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 4
        np.testing.assert_allclose(cc.median_abs_error, 1.0)
        np.testing.assert_allclose(cc.mean_bias, 0.5)

    def test_zero_device_pairs_are_dropped(self):
        """Pairs where device average == 0 must be excluded."""
        snore = np.array([10.0, 12.0, 8.0])
        device = np.array([0.0, 11.0, 0.0])  # first and last are zero → drop
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 1
        # Only the middle pair survives: diff = 12 - 11 = 1
        np.testing.assert_allclose(cc.median_abs_error, 1.0)

    def test_nan_pairs_are_dropped(self):
        snore = np.array([np.nan, 12.0, 8.0])
        device = np.array([9.0, np.nan, 7.0])
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 1  # only (8.0, 7.0) survives
        np.testing.assert_allclose(cc.median_abs_error, 1.0)

    def test_inf_values_are_dropped(self):
        """Inf on either side must be excluded (isfinite guard)."""
        snore = np.array([10.0, np.inf, 8.0])
        device = np.array([9.0, 11.0, np.inf])
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 1  # only (10.0, 9.0) survives
        np.testing.assert_allclose(cc.median_abs_error, 1.0)

    def test_empty_after_masking_returns_zero_pairs(self):
        snore = np.array([1.0, 2.0])
        device = np.array([0.0, np.nan])
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 0
        assert cc.spearman_r is None
        assert cc.median_abs_error is None
        assert cc.mean_bias is None

    def test_spearman_computed_for_n_ge_3(self):
        snore = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        device = np.array([1.5, 2.5, 3.5, 4.5, 5.5])
        cc = _compute_channel_metrics(snore, device)
        assert cc.spearman_r is not None
        assert abs(cc.spearman_r - 1.0) < 1e-6

    def test_spearman_none_for_n_lt_3(self):
        snore = np.array([1.0, 2.0])
        device = np.array([1.5, 2.5])
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 2
        assert cc.spearman_r is None

    def test_all_nan_snore(self):
        snore = np.full(5, np.nan)
        device = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        cc = _compute_channel_metrics(snore, device)
        assert cc.n_pairs == 0

    def test_constant_input_spearman_returns_none(self):
        """Constant SNORE values produce NaN spearman → None returned."""
        snore = np.ones(10) * 20.0
        device = np.arange(10, dtype=float) + 1.0
        cc = _compute_channel_metrics(snore, device)
        # spearman_or_none handles degenerate constant input
        assert cc.spearman_r is None
        assert cc.spearman_p is None


# ---------------------------------------------------------------------------
# Validator session-level skip paths (mock DB)
# ---------------------------------------------------------------------------


def _make_mock_session_row(
    session_id: int = 1, parser_version: str | None = "1.0.0"
) -> MagicMock:
    row = MagicMock()
    row.id = session_id
    row.start_time.strftime = lambda fmt: "2025-06-01"
    row.duration_seconds = 28800.0
    row.parser_version = parser_version
    return row


def _make_analysis_row(ar_id: int = 99) -> MagicMock:
    from snore.analysis.shared.versioning import (  # noqa: PLC0415
        AlgorithmIdentity,
        AlgoVersions,
        AnalysisRunMetadata,
    )

    algo_versions = AlgoVersions(
        identity=AlgorithmIdentity.current(),
        run=AnalysisRunMetadata(primary_mode="aasm", modes=["aasm"]),
    )
    row = MagicMock()
    row.engine_versions_json = algo_versions.model_dump()
    row.id = ar_id
    return row


def _make_execute_result(items: list, *, scalars_first: bool = False) -> MagicMock:
    """Build a mock execute result that supports both .scalars().all() and .scalars().first()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    result.scalars.return_value.first.return_value = items[0] if items else None
    return result


class TestBreathTrendsValidatorSkipPaths:
    @pytest.mark.asyncio
    async def test_skip_no_analysis(self, mock_db_session):
        """Session with no completed analysis → skipped_reason = 'no_analysis'."""
        session_row = _make_mock_session_row(1)

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([])  # no analysis row

        mock_db_session.execute = AsyncMock(
            side_effect=[sessions_result, analysis_result]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        s = report.sessions[0]
        assert s.skipped_reason == "no_analysis"
        assert s.channels == {}

    @pytest.mark.asyncio
    async def test_parser_version_null_falls_back_to_unknown(self, mock_db_session):
        session_row = _make_mock_session_row(1, parser_version=None)

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([])

        mock_db_session.execute = AsyncMock(
            side_effect=[sessions_result, analysis_result]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert report.sessions[0].parser_version == "unknown"

    @pytest.mark.asyncio
    async def test_skip_no_valid_breaths(self, mock_db_session):
        """Session with analysis but no leak-valid breaths → 'no_valid_breaths'."""
        session_row = _make_mock_session_row(2)
        analysis_row = _make_analysis_row(99)

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([analysis_row])
        breaths_result = _make_execute_result([])

        mock_db_session.execute = AsyncMock(
            side_effect=[sessions_result, analysis_result, breaths_result]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        s = report.sessions[0]
        assert s.skipped_reason == "no_valid_breaths"

    @pytest.mark.asyncio
    async def test_exception_during_session_appends_error_skip(self, mock_db_session):
        """Unhandled exception per session → synthetic result with skipped_reason='error'."""
        session_row = _make_mock_session_row(1)
        sessions_result = _make_execute_result([session_row])

        # Second execute raises, simulating an unexpected failure
        mock_db_session.execute = AsyncMock(
            side_effect=[sessions_result, RuntimeError("db failure")]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        assert report.sessions[0].skipped_reason == "error"
        assert report.aggregate.total_sessions == 1
        assert report.aggregate.sessions_compared == 0

    @pytest.mark.asyncio
    async def test_empty_date_range_returns_empty_report(self, mock_db_session):
        sessions_result = _make_execute_result([])
        mock_db_session.execute = AsyncMock(return_value=sessions_result)

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert report.aggregate.total_sessions == 0
        assert report.aggregate.sessions_compared == 0
        assert report.sessions == []

    @pytest.mark.asyncio
    async def test_channel_not_recorded_when_no_waveform_row(self, mock_db_session):
        """Channel with no device waveform row → skipped_reason = 'channel_not_recorded'."""
        session_row = _make_mock_session_row(3)
        analysis_row = _make_analysis_row(77)

        breath_mock = MagicMock()
        breath_mock.start_offset_s = 0.0
        breath_mock.end_offset_s = 3.0
        breath_mock.tidal_volume_ml = 500.0
        breath_mock.inspiration_time_s = 1.0
        breath_mock.expiration_time_s = 2.0
        breath_mock.breath_number = 1

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([analysis_row])
        breaths_result = _make_execute_result([breath_mock])
        # Single waveform query returns empty list (no channel waveforms)
        waveform_result = _make_execute_result([])

        mock_db_session.execute = AsyncMock(
            side_effect=[
                sessions_result,
                analysis_result,
                breaths_result,
                waveform_result,
            ]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        s = report.sessions[0]
        assert s.skipped_reason is None
        assert s.n_breaths == 1

        for ch in ("rr", "tv", "ti", "ie_ratio"):
            cc = s.channels[ch]
            assert cc.skipped_reason == "channel_not_recorded"
            assert cc.n_pairs == 0

    @pytest.mark.asyncio
    async def test_channel_not_recorded_when_sample_count_is_zero(
        self, mock_db_session
    ):
        """Waveform row exists but sample_count=0 → 'channel_not_recorded'."""
        session_row = _make_mock_session_row(4)
        analysis_row = _make_analysis_row(88)

        breath_mock = MagicMock()
        breath_mock.start_offset_s = 0.0
        breath_mock.end_offset_s = 3.0
        breath_mock.tidal_volume_ml = 500.0
        breath_mock.inspiration_time_s = 1.0
        breath_mock.expiration_time_s = 2.0
        breath_mock.breath_number = 1

        rr_waveform = MagicMock()
        rr_waveform.waveform_type = "rr"
        rr_waveform.sample_count = 0
        rr_waveform.data_blob = None

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([analysis_row])
        breaths_result = _make_execute_result([breath_mock])
        waveform_result = _make_execute_result([rr_waveform])

        mock_db_session.execute = AsyncMock(
            side_effect=[
                sessions_result,
                analysis_result,
                breaths_result,
                waveform_result,
            ]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        s = report.sessions[0]
        assert s.skipped_reason is None
        rr_cc = s.channels["rr"]
        assert rr_cc.skipped_reason == "channel_not_recorded"

    @pytest.mark.asyncio
    async def test_channel_not_recorded_when_data_blob_is_none(self, mock_db_session):
        """Waveform row exists with sample_count>0 but data_blob=None → 'channel_not_recorded'."""
        session_row = _make_mock_session_row(5)
        analysis_row = _make_analysis_row(55)

        breath_mock = MagicMock()
        breath_mock.start_offset_s = 0.0
        breath_mock.end_offset_s = 3.0
        breath_mock.tidal_volume_ml = 500.0
        breath_mock.inspiration_time_s = 1.0
        breath_mock.expiration_time_s = 2.0
        breath_mock.breath_number = 1

        tv_waveform = MagicMock()
        tv_waveform.waveform_type = "tv"
        tv_waveform.sample_count = 10
        tv_waveform.data_blob = None  # blob absent despite non-zero count

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([analysis_row])
        breaths_result = _make_execute_result([breath_mock])
        waveform_result = _make_execute_result([tv_waveform])

        mock_db_session.execute = AsyncMock(
            side_effect=[
                sessions_result,
                analysis_result,
                breaths_result,
                waveform_result,
            ]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        tv_cc = report.sessions[0].channels["tv"]
        assert tv_cc.skipped_reason == "channel_not_recorded"


# ---------------------------------------------------------------------------
# End-to-end numeric pipeline test
# ---------------------------------------------------------------------------


def _make_waveform_blob(timestamps: list[float], values: list[float]) -> bytes:
    """Pack [t, v] pairs into a float32 blob as deserialize_waveform_blob expects."""
    data = np.array(
        [[t, v] for t, v in zip(timestamps, values, strict=True)], dtype=np.float32
    )
    return data.tobytes()


class TestValidateSessionFullNumericPipeline:
    @pytest.mark.asyncio
    async def test_validate_session_full_numeric_pipeline(self, mock_db_session):
        """
        Drives _validate_session with synthetic TV waveform and 5 breaths with
        known tidal volumes.  Asserts exact n_pairs, mae, bias, and spearman_r.

        Breaths (each 2 s wide, starting at 0, 2, 4, 6, 8):
          TV = [300, 400, 500, 600, 700] mL
        TV waveform (one sample per breath at mid-breath):
          timestamps: [1, 3, 5, 7, 9]
          values:     [290, 392, 498, 606, 714]
        Expected:
          diff = [10, 8, 2, -6, -14]
          mae  = median(abs(diff)) = median([10, 8, 2, 6, 14]) = 8.0
          bias = mean(diff) = 0/5 = 0.0
          spearman_r ≈ 1.0 (both sides strictly increasing)
        """
        session_row = _make_mock_session_row(10)
        analysis_row = _make_analysis_row(200)

        tv_values = [300.0, 400.0, 500.0, 600.0, 700.0]
        breaths = []
        for i, tv in enumerate(tv_values):
            b = MagicMock()
            b.start_offset_s = float(i * 2)
            b.end_offset_s = float(i * 2 + 2)
            b.tidal_volume_ml = tv
            b.inspiration_time_s = 1.0
            b.expiration_time_s = 1.0
            b.breath_number = i
            breaths.append(b)

        # One sample per breath at mid-point
        device_ts = [1.0, 3.0, 5.0, 7.0, 9.0]
        device_tv = [290.0, 392.0, 498.0, 606.0, 714.0]
        blob = _make_waveform_blob(device_ts, device_tv)

        tv_waveform = MagicMock()
        tv_waveform.waveform_type = "tv"
        tv_waveform.sample_count = len(device_ts)
        tv_waveform.data_blob = blob

        sessions_result = _make_execute_result([session_row])
        analysis_result = _make_execute_result([analysis_row])
        breaths_result = _make_execute_result(breaths)
        waveform_result = _make_execute_result([tv_waveform])

        mock_db_session.execute = AsyncMock(
            side_effect=[
                sessions_result,
                analysis_result,
                breaths_result,
                waveform_result,
            ]
        )

        validator = BreathTrendsValidator(mock_db_session, profile_id=1)
        report = await validator.validate_date_range("2025-06-01", "2025-06-30")

        assert len(report.sessions) == 1
        s = report.sessions[0]
        assert s.skipped_reason is None
        assert s.n_breaths == 5

        # Channels without a waveform row → channel_not_recorded
        for ch in ("rr", "ti", "ie_ratio"):
            assert s.channels[ch].skipped_reason == "channel_not_recorded"

        tv_cc = s.channels["tv"]
        assert tv_cc.skipped_reason is None
        assert tv_cc.n_pairs == 5
        np.testing.assert_allclose(tv_cc.median_abs_error, 8.0, atol=0.5)
        np.testing.assert_allclose(tv_cc.mean_bias, 0.0, atol=0.5)
        assert tv_cc.spearman_r is not None
        assert tv_cc.spearman_r > 0.99
