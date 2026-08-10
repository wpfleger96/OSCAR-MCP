"""
Breath-to-waveform alignment helpers.

Generic utility for averaging a continuous waveform over per-breath windows.
Reusable across FL validation and future breath-trends validators.
"""

import numpy as np


def average_waveform_over_breaths(
    breath_starts: np.ndarray,
    breath_ends: np.ndarray,
    waveform_timestamps: np.ndarray,
    waveform_values: np.ndarray,
) -> np.ndarray:
    """Average a waveform signal over per-breath time windows.

    For each breath window [start, end), computes the mean of waveform samples
    whose timestamp falls in the window.  A window containing zero samples
    yields NaN.  Assumes waveform_timestamps is sorted ascending.

    Args:
        breath_starts: 1-D array of breath start offsets (seconds, session-relative).
        breath_ends: 1-D array of breath end offsets (seconds, session-relative).
        waveform_timestamps: 1-D sorted array of waveform sample timestamps.
        waveform_values: 1-D array of waveform values, parallel to timestamps.

    Returns:
        1-D float64 array of length len(breath_starts); element i is the mean
        waveform value over breath i, or NaN if no samples fell in that window.
    """
    n = len(breath_starts)
    result = np.full(n, np.nan, dtype=np.float64)

    if len(waveform_timestamps) == 0 or n == 0:
        return result

    values_f64 = waveform_values.astype(np.float64)

    for i in range(n):
        lo = np.searchsorted(waveform_timestamps, breath_starts[i], side="left")
        hi = np.searchsorted(waveform_timestamps, breath_ends[i], side="left")
        if hi > lo:
            result[i] = values_f64[lo:hi].mean()

    return result
