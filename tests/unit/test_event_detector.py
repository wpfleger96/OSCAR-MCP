"""
Tests for respiratory event detection.
"""

import numpy as np
import pytest

from oscar_mcp.analysis.algorithms.event_detector import (
    ApneaEvent,
    HypopneaEvent,
    RERAEvent,
    RespiratoryEventDetector,
)


class TestApneaDetection:
    """Test apnea detection functionality."""

    @pytest.fixture
    def detector(self):
        return RespiratoryEventDetector(min_event_duration=10.0)

    def test_detect_apnea_basic(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 1
        apnea = apneas[0]
        assert 9.9 <= apnea.duration <= 15.1
        assert apnea.flow_reduction >= 0.9
        assert 0.0 < apnea.confidence <= 1.0

    def test_detect_apnea_minimum_duration(self, detector):
        timestamps = np.arange(0, 20, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[50:95] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 0

    def test_detect_apnea_borderline_reduction(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 3.5

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 0

    def test_classify_apnea_obstructive(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 1.0

        effort_signal = np.sin(np.arange(len(timestamps)) * 0.1) * 0.8

        baseline_flow = 30.0

        apneas = detector.detect_apneas(
            timestamps, flow_values, baseline_flow=baseline_flow, effort_signal=effort_signal
        )

        assert len(apneas) == 1
        assert apneas[0].event_type == "OA"

    def test_classify_apnea_central(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 1.0

        effort_signal = np.sin(np.arange(len(timestamps)) * 0.1) * 0.05

        baseline_flow = 30.0

        apneas = detector.detect_apneas(
            timestamps, flow_values, baseline_flow=baseline_flow, effort_signal=effort_signal
        )

        assert len(apneas) == 1
        assert apneas[0].event_type == "CA"

    def test_classify_apnea_mixed(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 1.0

        effort_signal = np.sin(np.arange(len(timestamps)) * 0.1) * 0.3

        baseline_flow = 30.0

        apneas = detector.detect_apneas(
            timestamps, flow_values, baseline_flow=baseline_flow, effort_signal=effort_signal
        )

        assert len(apneas) == 1
        assert apneas[0].event_type == "MA"

    def test_classify_apnea_unclassified_no_effort(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 1
        assert apneas[0].event_type == "UA"

    def test_detect_multiple_apneas(self, detector):
        timestamps = np.arange(0, 100, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0

        flow_values[100:250] = 1.0
        flow_values[400:550] = 1.0
        flow_values[700:850] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 3

    def test_apnea_confidence_high_reduction(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 40.0
        flow_values[100:250] = 0.5

        baseline_flow = 40.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 1
        assert apneas[0].confidence >= 0.8


class TestHypopneaDetection:
    """Test hypopnea detection functionality."""

    @pytest.fixture
    def detector(self):
        return RespiratoryEventDetector(min_event_duration=10.0)

    def test_detect_hypopnea_basic(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 15.0

        baseline_flow = 30.0

        hypopneas = detector.detect_hypopneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(hypopneas) == 1
        hypopnea = hypopneas[0]
        assert 9.9 <= hypopnea.duration <= 15.1
        assert 0.3 <= hypopnea.flow_reduction < 0.9
        assert 0.0 < hypopnea.confidence <= 1.0

    def test_detect_hypopnea_with_desaturation(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 15.0

        spo2_signal = np.ones(len(timestamps)) * 95.0
        spo2_signal[120:200] = 91.0

        baseline_flow = 30.0

        hypopneas = detector.detect_hypopneas(
            timestamps, flow_values, baseline_flow=baseline_flow, spo2_signal=spo2_signal
        )

        assert len(hypopneas) == 1
        assert hypopneas[0].has_desaturation
        assert hypopneas[0].confidence >= 0.7

    def test_detect_hypopnea_without_desaturation(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 15.0

        spo2_signal = np.ones(len(timestamps)) * 95.0
        spo2_signal[120:200] = 94.0

        baseline_flow = 30.0

        hypopneas = detector.detect_hypopneas(
            timestamps, flow_values, baseline_flow=baseline_flow, spo2_signal=spo2_signal
        )

        assert len(hypopneas) == 1
        assert not hypopneas[0].has_desaturation

    def test_detect_hypopnea_minimum_reduction(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 21.5

        baseline_flow = 30.0

        hypopneas = detector.detect_hypopneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(hypopneas) == 0

    def test_detect_hypopnea_excludes_apneas(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 2.0

        baseline_flow = 30.0

        hypopneas = detector.detect_hypopneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(hypopneas) == 0

    def test_detect_multiple_hypopneas(self, detector):
        timestamps = np.arange(0, 100, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0

        flow_values[100:250] = 12.0
        flow_values[400:550] = 15.0
        flow_values[700:850] = 18.0

        baseline_flow = 30.0

        hypopneas = detector.detect_hypopneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(hypopneas) == 3


class TestRERADetection:
    """Test RERA detection functionality."""

    @pytest.fixture
    def detector(self):
        return RespiratoryEventDetector(min_event_duration=10.0)

    def test_detect_rera_basic(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 28.0

        flatness_indices = np.zeros(len(timestamps))
        flatness_indices[100:250] = 0.8

        baseline_flow = 30.0

        reras = detector.detect_reras(
            timestamps, flow_values, flatness_indices, baseline_flow=baseline_flow
        )

        assert len(reras) == 1
        rera = reras[0]
        assert 9.9 <= rera.duration <= 15.1
        assert rera.flatness_index >= 0.7
        assert 0.0 < rera.confidence <= 1.0

    def test_detect_rera_high_flatness(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 28.0

        flatness_indices = np.zeros(len(timestamps))
        flatness_indices[100:250] = 0.85

        baseline_flow = 30.0

        reras = detector.detect_reras(
            timestamps, flow_values, flatness_indices, baseline_flow=baseline_flow
        )

        assert len(reras) == 1
        assert reras[0].confidence >= 0.6

    def test_detect_rera_excludes_apneas(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 2.0

        flatness_indices = np.zeros(len(timestamps))
        flatness_indices[100:250] = 0.8

        baseline_flow = 30.0

        reras = detector.detect_reras(
            timestamps, flow_values, flatness_indices, baseline_flow=baseline_flow
        )

        assert len(reras) == 0

    def test_detect_rera_minimum_flatness(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0
        flow_values[100:250] = 28.0

        flatness_indices = np.zeros(len(timestamps))
        flatness_indices[100:250] = 0.65

        baseline_flow = 30.0

        reras = detector.detect_reras(
            timestamps, flow_values, flatness_indices, baseline_flow=baseline_flow
        )

        assert len(reras) == 0


class TestEventMerging:
    """Test event merging functionality."""

    @pytest.fixture
    def detector(self):
        return RespiratoryEventDetector(min_event_duration=10.0, merge_gap=2.0)

    def test_merge_adjacent_apneas(self, detector):
        timestamps = np.arange(0, 50, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0

        flow_values[100:250] = 1.0
        flow_values[265:415] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 1
        assert apneas[0].duration > 20.0

    def test_no_merge_distant_apneas(self, detector):
        timestamps = np.arange(0, 100, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0

        flow_values[100:250] = 1.0
        flow_values[400:550] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 2

    def test_merge_preserves_confidence(self, detector):
        timestamps = np.arange(0, 50, 0.1)
        flow_values = np.ones(len(timestamps)) * 30.0

        flow_values[100:250] = 0.5
        flow_values[265:415] = 1.0

        baseline_flow = 30.0

        apneas = detector.detect_apneas(timestamps, flow_values, baseline_flow=baseline_flow)

        assert len(apneas) == 1
        assert 0.0 < apneas[0].confidence <= 1.0


class TestEventTimeline:
    """Test event timeline creation and AHI/RDI calculation."""

    @pytest.fixture
    def detector(self):
        return RespiratoryEventDetector(min_event_duration=10.0)

    def test_create_timeline_basic(self, detector):
        apneas = [
            ApneaEvent(
                start_time=10.0,
                end_time=22.0,
                duration=12.0,
                event_type="OA",
                flow_reduction=0.95,
                confidence=0.85,
                baseline_flow=30.0,
            ),
            ApneaEvent(
                start_time=50.0,
                end_time=65.0,
                duration=15.0,
                event_type="CA",
                flow_reduction=0.97,
                confidence=0.90,
                baseline_flow=30.0,
            ),
        ]

        hypopneas = [
            HypopneaEvent(
                start_time=100.0,
                end_time=112.0,
                duration=12.0,
                flow_reduction=0.55,
                confidence=0.75,
                baseline_flow=30.0,
            )
        ]

        reras = [
            RERAEvent(
                start_time=200.0,
                end_time=215.0,
                duration=15.0,
                flatness_index=0.82,
                confidence=0.70,
            )
        ]

        session_duration_hours = 8.0

        timeline = detector.create_event_timeline(apneas, hypopneas, reras, session_duration_hours)

        assert timeline.total_events == 4
        assert len(timeline.apneas) == 2
        assert len(timeline.hypopneas) == 1
        assert len(timeline.reras) == 1
        assert timeline.ahi == 3.0 / 8.0
        assert timeline.rdi == 4.0 / 8.0

    def test_calculate_ahi_correct(self, detector):
        apneas = [
            ApneaEvent(10.0, 22.0, 12.0, "OA", 0.95, 0.85, 30.0),
            ApneaEvent(50.0, 65.0, 15.0, "CA", 0.97, 0.90, 30.0),
        ]
        hypopneas = [
            HypopneaEvent(100.0, 112.0, 12.0, 0.55, 0.75, 30.0),
            HypopneaEvent(150.0, 162.0, 12.0, 0.60, 0.80, 30.0),
        ]
        reras = []

        session_duration_hours = 2.0

        timeline = detector.create_event_timeline(apneas, hypopneas, reras, session_duration_hours)

        assert timeline.ahi == 4.0 / 2.0
        assert timeline.ahi == 2.0

    def test_calculate_rdi_correct(self, detector):
        apneas = [ApneaEvent(10.0, 22.0, 12.0, "OA", 0.95, 0.85, 30.0)]
        hypopneas = [HypopneaEvent(100.0, 112.0, 12.0, 0.55, 0.75, 30.0)]
        reras = [RERAEvent(200.0, 215.0, 15.0, 0.82, 0.70)]

        session_duration_hours = 1.0

        timeline = detector.create_event_timeline(apneas, hypopneas, reras, session_duration_hours)

        assert timeline.rdi == 3.0 / 1.0
        assert timeline.rdi == 3.0

    def test_handle_zero_duration(self, detector):
        apneas = []
        hypopneas = []
        reras = []

        session_duration_hours = 0.0

        timeline = detector.create_event_timeline(apneas, hypopneas, reras, session_duration_hours)

        assert timeline.ahi == 0.0
        assert timeline.rdi == 0.0


class TestHelperMethods:
    """Test helper methods for baseline calculation and flow reduction."""

    @pytest.fixture
    def detector(self):
        return RespiratoryEventDetector(min_event_duration=10.0)

    def test_calculate_baseline_flow_normal(self, detector):
        flow_values = np.array([20.0, 25.0, 30.0, 35.0, 40.0, 30.0, 25.0])

        baseline = detector._calculate_baseline_flow(flow_values)

        assert baseline == 30.0

    def test_calculate_baseline_flow_with_zeros(self, detector):
        flow_values = np.array([0.0, 20.0, 25.0, 30.0, 0.0, 25.0, 20.0])

        baseline = detector._calculate_baseline_flow(flow_values)

        assert baseline == 25.0

    def test_calculate_baseline_flow_all_zeros(self, detector):
        flow_values = np.array([0.0, 0.0, 0.0, 0.0])

        baseline = detector._calculate_baseline_flow(flow_values)

        assert baseline == 1.0

    def test_calculate_flow_reduction_normal(self, detector):
        flow_values = np.array([30.0, 20.0, 10.0, 5.0, 1.0])
        baseline = 30.0

        reduction = detector._calculate_flow_reduction(flow_values, baseline)

        expected = np.array([0.0, 1.0 / 3.0, 2.0 / 3.0, 5.0 / 6.0, 29.0 / 30.0])
        np.testing.assert_array_almost_equal(reduction, expected, decimal=2)

    def test_calculate_flow_reduction_negative_flow(self, detector):
        flow_values = np.array([30.0, -30.0, 15.0, -15.0])
        baseline = 30.0

        reduction = detector._calculate_flow_reduction(flow_values, baseline)

        expected = np.array([0.0, 0.0, 0.5, 0.5])
        np.testing.assert_array_almost_equal(reduction, expected, decimal=2)

    def test_calculate_flow_reduction_zero_baseline(self, detector):
        flow_values = np.array([10.0, 20.0, 30.0])
        baseline = 0.0

        reduction = detector._calculate_flow_reduction(flow_values, baseline)

        expected = np.array([0.0, 0.0, 0.0])
        np.testing.assert_array_equal(reduction, expected)

    def test_find_continuous_regions_basic(self, detector):
        timestamps = np.arange(0, 30, 0.1)
        condition = np.zeros(len(timestamps), dtype=bool)
        condition[100:250] = True

        regions = detector._find_continuous_regions(timestamps, condition, min_duration=10.0)

        assert len(regions) == 1
        assert regions[0][2] >= 10.0

    def test_find_continuous_regions_too_short(self, detector):
        timestamps = np.arange(0, 20, 0.1)
        condition = np.zeros(len(timestamps), dtype=bool)
        condition[50:95] = True

        regions = detector._find_continuous_regions(timestamps, condition, min_duration=10.0)

        assert len(regions) == 0

    def test_find_continuous_regions_multiple(self, detector):
        timestamps = np.arange(0, 100, 0.1)
        condition = np.zeros(len(timestamps), dtype=bool)
        condition[100:250] = True
        condition[400:550] = True
        condition[700:850] = True

        regions = detector._find_continuous_regions(timestamps, condition, min_duration=10.0)

        assert len(regions) == 3
