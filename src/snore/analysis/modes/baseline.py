"""Baseline calculation for respiratory event detection."""

from typing import Any

import numpy as np

from snore.analysis.modes.config import DetectionModeConfig
from snore.analysis.modes.types import BaselineMethod


def _calculate_baseline(
    config: DetectionModeConfig, breaths: list[Any], current_idx: int
) -> float:
    """Calculate baseline using configured method."""
    if config.baseline_method == BaselineMethod.TIME:
        return _calculate_time_based_baseline(config, breaths, current_idx)
    else:
        return _calculate_breath_based_baseline(config, breaths, current_idx)


def _calculate_time_based_baseline(
    config: DetectionModeConfig, breaths: list[Any], current_idx: int
) -> float:
    """
    Calculate baseline from breaths within time window (AASM-compliant).

    Uses a time-based window (default 2 minutes per AASM) of preceding breaths
    to calculate baseline, excluding breaths that are part of detected events.

    Args:
        config: Detection mode configuration
        breaths: List of BreathMetrics objects
        current_idx: Index of current breath

    Returns:
        Baseline value, minimum 10.0 for amplitude or 100.0 for tidal_volume
    """
    if current_idx == 0:
        return 30.0 if config.metric == "amplitude" else 300.0

    current_breath = breaths[current_idx]
    current_time = current_breath.start_time
    window_start = current_time - config.baseline_window

    values = []
    for i in range(current_idx - 1, -1, -1):
        breath = breaths[i]
        if breath.start_time < window_start:
            break

        if not breath.in_event:
            if config.metric == "amplitude":
                if breath.amplitude > 0:
                    values.append(breath.amplitude)
            elif config.metric == "tidal_volume":
                if breath.tidal_volume > 0:
                    values.append(breath.tidal_volume)

    if len(values) < 5:
        return 30.0 if config.metric == "amplitude" else 300.0

    baseline = float(np.percentile(values, config.baseline_percentile))
    min_baseline = 10.0 if config.metric == "amplitude" else 100.0
    return max(baseline, min_baseline)


def _calculate_breath_based_baseline(
    config: DetectionModeConfig, breaths: list[Any], current_idx: int
) -> float:
    """
    Calculate baseline from preceding breath count.

    Uses a rolling window of recent breaths to calculate baseline,
    excluding breaths that are part of detected events.

    Args:
        config: Detection mode configuration
        breaths: List of BreathMetrics objects
        current_idx: Index of current breath

    Returns:
        Baseline value, minimum 10.0 for amplitude or 100.0 for tidal_volume
    """
    if current_idx == 0:
        return 30.0 if config.metric == "amplitude" else 300.0

    window_breaths = int(config.baseline_window)  # baseline_window is breath count
    start_idx = max(0, current_idx - window_breaths)
    window = breaths[start_idx:current_idx]

    if len(window) < 5:
        return 30.0 if config.metric == "amplitude" else 300.0

    values = []
    for b in window:
        if config.metric == "amplitude":
            if b.amplitude > 0 and not b.in_event:
                values.append(b.amplitude)
        elif config.metric == "tidal_volume":
            if b.tidal_volume > 0 and not b.in_event:
                values.append(b.tidal_volume)

    if not values:
        return 30.0 if config.metric == "amplitude" else 300.0

    baseline = float(np.percentile(values, config.baseline_percentile))
    min_baseline = 10.0 if config.metric == "amplitude" else 100.0
    return max(baseline, min_baseline)
