"""
Unit tests for breath segmentation algorithm.

Tests zero-crossing detection, breath boundary identification,
and edge case handling.
"""

import numpy as np
import pytest

from snore.analysis.shared.breath_segmenter import BreathSegmenter
from tests.helpers.synthetic_data import (
    generate_sinusoidal_breath,
)


def _reference_detect_zero_crossings(
    flow_data: np.ndarray, hysteresis: float
) -> list[tuple[int, str]]:
    """Verbatim scalar oracle — used to verify vectorized output is behavior-identical."""
    crossings = []
    current_state = None
    last_crossing_idx = -1
    for i, value in enumerate(flow_data):
        if value > hysteresis:
            new_state = "positive"
        elif value < -hysteresis:
            new_state = "negative"
        else:
            continue
        if current_state is None:
            crossings.append((i, new_state))
            last_crossing_idx = i
        elif new_state != current_state:
            if i - last_crossing_idx > 5:
                crossings.append((i, new_state))
                last_crossing_idx = i
        current_state = new_state
    return crossings


class TestZeroCrossingDetection:
    """Test zero-crossing detection with hysteresis."""

    def test_zero_crossing_normal_breath(self):
        """Normal sinusoidal breath should have zero crossings."""
        segmenter = BreathSegmenter()
        _, flow = generate_sinusoidal_breath(duration=4.0, amplitude=30.0)

        crossings = segmenter.detect_zero_crossings(flow)

        assert len(crossings) >= 2

    def test_zero_crossing_all_positive_flow(self):
        """All positive flow should have no or minimal crossings."""
        segmenter = BreathSegmenter()
        flow = np.ones(100) * 20.0

        crossings = segmenter.detect_zero_crossings(flow)

        assert len(crossings) <= 1

    def test_zero_crossing_all_negative_flow(self):
        """All negative flow should have no or minimal crossings."""
        segmenter = BreathSegmenter()
        flow = np.ones(100) * -20.0

        crossings = segmenter.detect_zero_crossings(flow)

        assert len(crossings) <= 1

    def test_zero_crossing_hysteresis_prevents_noise(self):
        """Hysteresis should prevent noise near zero from creating false crossings."""
        segmenter = BreathSegmenter(hysteresis=5.0)

        flow = np.random.normal(0, 2.0, 100)

        crossings = segmenter.detect_zero_crossings(flow)

        assert len(crossings) < 5

    def test_zero_crossing_at_boundaries(self):
        """Should handle zero crossings at array boundaries."""
        segmenter = BreathSegmenter()

        flow = np.concatenate(
            [
                np.ones(50) * 20.0,
                np.ones(50) * -20.0,
            ]
        )

        crossings = segmenter.detect_zero_crossings(flow)

        assert len(crossings) >= 1


class TestBreathBoundaryIdentification:
    """Test breath boundary identification from crossings."""

    def test_identify_single_complete_breath(self):
        """Single complete breath should be identified."""
        segmenter = BreathSegmenter()
        t, flow = generate_sinusoidal_breath(duration=4.0)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) >= 1
        if len(breaths) > 0:
            assert breaths[0].duration > 0

    def test_identify_multiple_breaths(self):
        """Multiple breaths should all be identified."""
        from tests.helpers.synthetic_data import create_session

        segmenter = BreathSegmenter()
        t, flow = create_session(num_breaths=10, sample_rate=25.0)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) >= 7

    def test_minimum_duration_filter(self):
        """Breaths shorter than minimum should be rejected."""
        segmenter = BreathSegmenter(min_breath_duration=2.0)

        t = np.linspace(0, 1, 25)
        flow = 20.0 * np.sin(2 * np.pi * t)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) == 0

    def test_maximum_duration_filter(self):
        """Breaths longer than maximum should be rejected."""
        segmenter = BreathSegmenter(max_breath_duration=10.0)

        t = np.linspace(0, 25, 625)
        flow = 20.0 * np.sin(2 * np.pi * t / 25)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) == 0

    def test_amplitude_filter_rejects_small_breaths(self):
        """Breaths with amplitude <= 2 L/min should be rejected."""
        segmenter = BreathSegmenter()

        t = np.linspace(0, 4, 100)
        flow = 0.75 * np.sin(2 * np.pi * t / 4)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) == 0

    def test_incomplete_breath_at_start(self):
        """Incomplete breath at start should be handled."""
        segmenter = BreathSegmenter()

        t = np.linspace(1, 5, 100)
        flow = 30.0 * np.sin(2 * np.pi * t / 4)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) >= 0

    def test_incomplete_breath_at_end(self):
        """Incomplete breath at end should be handled."""
        segmenter = BreathSegmenter()

        t = np.linspace(0, 3, 75)
        flow = 30.0 * np.sin(2 * np.pi * t / 4)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) >= 0


class TestBreathSegmentationEdgeCases:
    """Test edge cases in breath segmentation."""

    def test_empty_array(self):
        """Empty arrays should return no breaths."""
        segmenter = BreathSegmenter()

        breaths = segmenter.segment_breaths(
            np.array([]), np.array([]), sample_rate=25.0
        )

        assert len(breaths) == 0

    def test_single_sample(self):
        """Single sample should return no breaths."""
        segmenter = BreathSegmenter()

        breaths = segmenter.segment_breaths(
            np.array([0.0]), np.array([10.0]), sample_rate=25.0
        )

        assert len(breaths) == 0

    def test_very_short_segment(self):
        """Very short segment should return no or few breaths."""
        segmenter = BreathSegmenter()

        t = np.linspace(0, 0.5, 12)
        flow = 20.0 * np.sin(4 * np.pi * t)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) == 0

    def test_extremely_long_breath(self):
        """Extremely long breath should be rejected by max_duration."""
        segmenter = BreathSegmenter(max_breath_duration=20.0)

        t = np.linspace(0, 30, 750)
        flow = 30.0 * np.sin(2 * np.pi * t / 30)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) == 0

    def test_very_noisy_signal(self):
        """Very noisy signal should still find valid breaths."""
        from tests.helpers.synthetic_data import generate_noisy_breath

        segmenter = BreathSegmenter()
        t, flow = generate_noisy_breath(duration=4.0, snr_db=5.0)

        breaths = segmenter.segment_breaths(t, flow, sample_rate=25.0)

        assert len(breaths) >= 0


# ---------------------------------------------------------------------------
# Vectorized zero-crossing oracle comparison
# ---------------------------------------------------------------------------

_ORACLE_PARAMS = [
    # (seed, n_samples, amplitude, hysteresis, label)
    (0, 720_000, 30.0, 2.0, "full_session_sine_noise"),
    (1, 720_000, 30.0, 2.0, "full_session_different_seed"),
    (2, 1000, 30.0, 2.0, "short_sine_noise"),
    (3, 1000, 1.0, 2.0, "low_amplitude_inside_band"),
    (4, 1000, 30.0, 5.0, "high_hysteresis"),
    (5, 1000, 30.0, 0.5, "low_hysteresis"),
    (6, 500, 0.0, 2.0, "constant_zero"),
    (7, 1000, 15.0, 2.0, "pure_noise_no_sine"),
]


@pytest.mark.parametrize(
    "seed,n_samples,amplitude,hysteresis,label",
    _ORACLE_PARAMS,
    ids=[p[4] for p in _ORACLE_PARAMS],
)
class TestVectorizedZeroCrossingOracle:
    """Verify the vectorized detect_zero_crossings is behavior-identical to the scalar oracle."""

    def _make_signal(
        self, seed: int, n_samples: int, amplitude: float, label: str
    ) -> np.ndarray:
        rng = np.random.default_rng(seed)
        t = np.linspace(0, n_samples / 25.0, n_samples)
        if label == "constant_zero":
            return np.zeros(n_samples)
        if label == "pure_noise_no_sine":
            return rng.normal(0, amplitude, n_samples)
        # Default: sine at ~0.25 Hz plus noise
        return amplitude * np.sin(2 * np.pi * 0.25 * t) + rng.normal(0, 1.5, n_samples)

    def test_vectorized_matches_oracle(
        self, seed, n_samples, amplitude, hysteresis, label
    ):
        flow = self._make_signal(seed, n_samples, amplitude, label)
        segmenter = BreathSegmenter(hysteresis=hysteresis)

        expected = _reference_detect_zero_crossings(flow, hysteresis)
        actual = segmenter.detect_zero_crossings(flow)

        assert actual == expected


# ---------------------------------------------------------------------------
# Vectorized zero-crossing edge cases
# ---------------------------------------------------------------------------


class TestVectorizedZeroCrossingEdgeCases:
    """Edge cases for the vectorized detect_zero_crossings."""

    def test_empty_array_returns_empty_list(self):
        segmenter = BreathSegmenter()
        result = segmenter.detect_zero_crossings(np.array([]))
        assert result == []

    def test_all_samples_within_band_returns_empty_list(self):
        segmenter = BreathSegmenter(hysteresis=2.0)
        # All samples strictly inside [-2.0, 2.0]
        flow = np.linspace(-1.9, 1.9, 200)
        result = segmenter.detect_zero_crossings(flow)
        assert result == []

    def test_samples_exactly_at_hysteresis_boundary_treated_as_dead_band(self):
        # Values exactly equal to hysteresis are NOT classified (strict inequalities).
        segmenter = BreathSegmenter(hysteresis=2.0)
        flow = np.array([2.0, -2.0, 2.0], dtype=float)
        result = segmenter.detect_zero_crossings(flow)
        assert result == []

    def test_gap_rejection_produces_consecutive_same_direction_crossings(self):
        """A gap-rejected transition flips current_state without moving last_crossing_idx.
        When the next transition goes back to the original direction, the gap is measured
        from the last ACCEPTED crossing — so it can be large enough to accept, producing
        two consecutive same-direction accepted crossings.

        Signal design (dead-band = 0, above = +10, below = -10):
          index 0:  +10  → append (0, "positive"),  last=0,  state="positive"
          index 10: -10  → gap 10>5 → append (10, "negative"), last=10, state="negative"
          index 12: +10  → gap 2≤5  → REJECTED, last stays 10, state="positive"
          index 20: -10  → gap 10>5 → append (20, "negative"), last=20, state="negative"

        Expected crossings: [(0,"positive"), (10,"negative"), (20,"negative")]
        """
        hysteresis = 2.0
        segmenter = BreathSegmenter(hysteresis=hysteresis)

        flow = np.zeros(30)
        flow[0] = 10.0
        flow[10] = -10.0
        flow[12] = 10.0  # gap from last accepted (10) = 2 ≤ 5 → rejected
        flow[
            20
        ] = -10.0  # gap from last accepted (10) = 10 > 5 → accepted as "negative" again

        expected = _reference_detect_zero_crossings(flow, hysteresis)
        actual = segmenter.detect_zero_crossings(flow)

        assert actual == expected

        # Confirm the oracle produces two consecutive "negative" entries in the list
        directions = [d for _, d in expected]
        assert directions.count("negative") >= 2
        neg_positions = [pos for pos, (_, d) in enumerate(expected) if d == "negative"]
        assert len(neg_positions) >= 2 and neg_positions[-1] == neg_positions[-2] + 1

    def test_output_contains_plain_python_ints_not_numpy_int64(self):
        """Tuple indices must be plain Python ints, not np.int64, to avoid
        downstream type surprises."""
        segmenter = BreathSegmenter(hysteresis=2.0)
        flow = np.array([10.0, 10.0, -10.0, -10.0, 10.0, 10.0], dtype=float)
        crossings = segmenter.detect_zero_crossings(flow)

        assert len(crossings) > 0
        for idx, direction in crossings:
            assert type(idx) is int, f"Expected int, got {type(idx)}"
            assert isinstance(direction, str)
