"""
Pulse change detection for CPAP session analysis.

Implements OSCAR's calcPulseChange algorithm (calcs.cpp:1407-1480) which detects
rapid changes in heart rate during sleep. Pulse changes can indicate arousals or
other sleep disturbances.
"""

import logging

from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PulseChangeEvent:
    """
    Represents a detected pulse rate change event.

    Attributes:
        start_time: Event start time in seconds from session start
        duration: Duration of the pulse change window in seconds
        bpm_delta: Absolute change in pulse rate (BPM)
    """

    start_time: float
    duration: float
    bpm_delta: float


class PulseChangeDetector:
    """
    Detector for rapid pulse rate changes during sleep.

    Uses a sliding window approach matching OSCAR's algorithm: for each pulse
    data point, scan forward within a duration threshold and detect if any point
    differs by >= bpm_threshold.

    Pulse data is typically 1Hz (~28,800 points for an 8h session), making this
    a trivially fast operation.
    """

    def __init__(
        self,
        bpm_threshold: float = 5.0,
        duration_threshold: float = 8.0,
    ):
        """
        Initialize pulse change detector.

        Args:
            bpm_threshold: Minimum BPM change to count as event (default 5.0)
            duration_threshold: Maximum time window in seconds to scan forward (default 8.0)
        """
        self.bpm_threshold = bpm_threshold
        self.duration_threshold = duration_threshold

    def detect(
        self,
        timestamps: np.ndarray,
        pulse_values: np.ndarray,
    ) -> list[PulseChangeEvent]:
        """
        Detect pulse change events in pulse rate waveform.

        Algorithm (from OSCAR calcs.cpp:1407-1480):
        1. For each pulse data point at time t
        2. Scan forward within duration_threshold seconds
        3. If any point differs by >= bpm_threshold BPM, record pulse change event
        4. Skip NaN/artifact values (pulse data has dropouts)

        Args:
            timestamps: Array of timestamp offsets in seconds from session start
            pulse_values: Array of pulse rate values in BPM

        Returns:
            List of PulseChangeEvent objects, sorted by start_time
        """
        if len(timestamps) == 0 or len(pulse_values) == 0:
            return []

        if len(timestamps) != len(pulse_values):
            logger.warning(
                f"Timestamp/value length mismatch: {len(timestamps)} vs {len(pulse_values)}"
            )
            return []

        events: list[PulseChangeEvent] = []

        valid_mask = ~np.isnan(pulse_values)
        valid_indices = np.where(valid_mask)[0]

        if len(valid_indices) == 0:
            logger.warning("No valid pulse data found (all NaN)")
            return []

        for i in valid_indices:
            current_time = timestamps[i]
            current_bpm = pulse_values[i]

            max_time = current_time + self.duration_threshold

            forward_mask = (
                (timestamps > current_time) & (timestamps <= max_time) & valid_mask
            )
            forward_indices = np.where(forward_mask)[0]

            if len(forward_indices) == 0:
                continue

            forward_bpm = pulse_values[forward_indices]
            bpm_deltas = np.abs(forward_bpm - current_bpm)

            max_delta_idx = np.argmax(bpm_deltas)
            max_delta = bpm_deltas[max_delta_idx]

            if max_delta >= self.bpm_threshold:
                forward_time = timestamps[forward_indices[max_delta_idx]]
                duration = forward_time - current_time

                events.append(
                    PulseChangeEvent(
                        start_time=current_time,
                        duration=duration,
                        bpm_delta=max_delta,
                    )
                )

        events.sort(key=lambda e: e.start_time)

        logger.debug(f"Detected {len(events)} pulse change events")

        return events
