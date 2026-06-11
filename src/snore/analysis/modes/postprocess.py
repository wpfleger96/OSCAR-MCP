"""Event post-processing: validation, deduplication and merging."""

import logging

from collections.abc import Sequence

import numpy as np

from snore.analysis.modes.config import DetectionModeConfig
from snore.analysis.shared.types import ApneaEvent, HypopneaEvent

logger = logging.getLogger(__name__)


def _calculate_event_overlap(event1: ApneaEvent, event2: ApneaEvent) -> float:
    """
    Calculate overlap ratio between two events.

    Args:
        event1: First apnea event
        event2: Second apnea event

    Returns:
        Overlap ratio (0.0-1.0) relative to shorter event duration
    """
    overlap_start = max(event1.start_time, event2.start_time)
    overlap_end = min(event1.end_time, event2.end_time)

    if overlap_start >= overlap_end:
        return 0.0

    overlap_duration = overlap_end - overlap_start
    shorter_duration = min(event1.duration, event2.duration)

    return overlap_duration / shorter_duration


def _validate_event(
    config: DetectionModeConfig,
    reductions: np.ndarray,
    start_idx: int,
    end_idx: int,
    threshold: float | None = None,
) -> bool:
    """
    Validate that event contains at least one breath meeting the threshold.

    Uses configured validation threshold.

    Args:
        config: Detection mode configuration
        reductions: Array of reduction values per breath (0.0-1.0)
        start_idx: Event start index in breaths array
        end_idx: Event end index in breaths array
        threshold: Override threshold (if None, uses config.apnea_validation_threshold)

    Returns:
        True if at least one breath meets the threshold
    """
    event_reductions = reductions[start_idx:end_idx]
    if len(event_reductions) == 0:
        return False

    max_reduction = float(np.max(event_reductions))

    validation_threshold = (
        threshold if threshold is not None else config.apnea_validation_threshold
    )
    return max_reduction >= validation_threshold


def _deduplicate_events(
    config: DetectionModeConfig,
    events: list[ApneaEvent],
    overlap_threshold: float = 0.5,
) -> list[ApneaEvent]:
    """
    Remove duplicate/overlapping events, keeping highest confidence.

    When multiple detection methods find the same event, keep the
    detection with highest confidence. Merge events that overlap
    by more than overlap_threshold (50% default).

    Args:
        config: Detection mode configuration
        events: List of apnea events (potentially overlapping)
        overlap_threshold: Minimum overlap ratio to consider duplicates (0.0-1.0)

    Returns:
        List of deduplicated events
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e.start_time)

    deduplicated = []
    current = sorted_events[0]

    for next_event in sorted_events[1:]:
        overlap = _calculate_event_overlap(current, next_event)

        if overlap > overlap_threshold:
            if next_event.confidence > current.confidence:
                logger.debug(
                    f"  Replacing {current.detection_method} event at {current.start_time:.1f}s "
                    f"(conf={current.confidence:.2f}) with {next_event.detection_method} "
                    f"(conf={next_event.confidence:.2f})"
                )
                current = next_event
            else:
                logger.debug(
                    f"  Keeping {current.detection_method} event at {current.start_time:.1f}s "
                    f"(conf={current.confidence:.2f}), dropping {next_event.detection_method} "
                    f"(conf={next_event.confidence:.2f})"
                )
        else:
            deduplicated.append(current)
            current = next_event

    deduplicated.append(current)

    if len(events) > len(deduplicated):
        logger.info(
            f"{config.name}: Deduplicated {len(events)} events to {len(deduplicated)}"
        )

    return deduplicated


def _merge_adjacent_events(
    events: Sequence[ApneaEvent | HypopneaEvent],
    max_gap: float,
) -> list[ApneaEvent | HypopneaEvent]:
    """
    Merge events that are close together in time AND of the same type.

    Per AASM standards, only events of the same type should be merged.

    Args:
        events: List of ApneaEvent or HypopneaEvent objects
        max_gap: Maximum gap in seconds to merge

    Returns:
        List of merged events
    """
    if len(events) <= 1:
        return list(events)

    merged = []
    current = events[0]

    for next_event in events[1:]:
        gap = next_event.start_time - current.end_time
        same_type = type(next_event) == type(current)

        if gap <= max_gap and same_type:
            current = _merge_two_events(current, next_event)
        else:
            merged.append(current)
            current = next_event

    merged.append(current)
    return merged


def _merge_two_events(
    event1: ApneaEvent | HypopneaEvent,
    event2: ApneaEvent | HypopneaEvent,
) -> ApneaEvent | HypopneaEvent:
    """
    Merge two adjacent events of the same type.

    Args:
        event1: First event
        event2: Second event

    Returns:
        Merged event
    """
    merged_duration = event2.end_time - event1.start_time

    if isinstance(event1, ApneaEvent) and isinstance(event2, ApneaEvent):
        return ApneaEvent(
            start_time=event1.start_time,
            end_time=event2.end_time,
            duration=merged_duration,
            event_type=(
                event1.event_type
                if event1.classification_confidence >= event2.classification_confidence
                else event2.event_type
            ),
            flow_reduction=(event1.flow_reduction + event2.flow_reduction) / 2,
            confidence=min(event1.confidence, event2.confidence),
            classification_confidence=min(
                event1.classification_confidence, event2.classification_confidence
            ),
            baseline_flow=event1.baseline_flow,
            detection_method=event1.detection_method,
        )
    elif isinstance(event1, HypopneaEvent) and isinstance(event2, HypopneaEvent):
        return HypopneaEvent(
            start_time=event1.start_time,
            end_time=event2.end_time,
            duration=merged_duration,
            flow_reduction=(event1.flow_reduction + event2.flow_reduction) / 2,
            confidence=min(event1.confidence, event2.confidence),
            baseline_flow=event1.baseline_flow,
            has_arousal=event1.has_arousal or event2.has_arousal,
            has_desaturation=event1.has_desaturation or event2.has_desaturation,
        )

    event_typed: ApneaEvent | HypopneaEvent = event1
    return event_typed
