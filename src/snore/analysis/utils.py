"""Analysis utility functions."""

from snore.analysis.shared.types import ApneaEvent, HypopneaEvent, RERAEvent
from snore.analysis.types import AnalysisEvent
from snore.constants import (
    EVENT_TYPE_HYPOPNEA,
    EVENT_TYPE_RERA,
    get_apnea_type,
    is_apnea_type,
)


def convert_machine_events(
    machine_events: list[AnalysisEvent],
) -> tuple[list[ApneaEvent], list[HypopneaEvent]]:
    """
    Convert raw machine AnalysisEvents to typed ApneaEvent/HypopneaEvent lists.

    Args:
        machine_events: List of machine-detected events with session-relative timestamps
                        (already converted from Unix timestamps in _load_machine_events)

    Returns:
        Tuple of (apnea_events, hypopnea_events)
    """
    apneas: list[ApneaEvent] = []
    hypopneas: list[HypopneaEvent] = []

    for event in machine_events:
        if is_apnea_type(event.event_type):
            apnea_type = get_apnea_type(event.event_type)
            if apnea_type:
                apneas.append(
                    ApneaEvent(
                        start_time=event.start_time,
                        end_time=event.start_time + event.duration,
                        duration=event.duration,
                        event_type=apnea_type,
                        flow_reduction=event.flow_reduction or 0.0,
                        confidence=event.confidence or 1.0,
                        classification_confidence=1.0,
                        baseline_flow=event.baseline_flow or 0.0,
                        detection_method="machine",
                    )
                )
        elif event.event_type == EVENT_TYPE_HYPOPNEA:
            hypopneas.append(
                HypopneaEvent(
                    start_time=event.start_time,
                    end_time=event.start_time + event.duration,
                    duration=event.duration,
                    flow_reduction=event.flow_reduction or 0.0,
                    confidence=event.confidence or 1.0,
                    baseline_flow=event.baseline_flow or 0.0,
                    has_desaturation=event.has_desaturation,
                    has_arousal=False,
                )
            )

    return apneas, hypopneas


def convert_machine_reras(machine_events: list[AnalysisEvent]) -> list[RERAEvent]:
    """
    Extract machine-flagged RERAs ("RE") as typed RERAEvent objects.

    A sibling of ``convert_machine_events`` rather than folded into it: folding
    in would widen that function's ``(apneas, hypopneas)`` return to a 3-tuple
    and break its three unrelated callers, which want only apneas/hypopneas.

    Machine RERA rows carry only timing; the shape fields RERAEvent requires
    (obstructed_breath_count, recovery_amplitude_increase_pct) are unavailable
    from the device, so they are filled with schema-valid placeholders. Only
    ``start_time`` participates in tolerance matching against programmatic RERAs.

    Args:
        machine_events: Machine-detected events with session-relative timestamps

    Returns:
        List of machine RERA events (empty if the device flagged none)
    """
    return [
        RERAEvent(
            start_time=event.start_time,
            end_time=event.start_time + event.duration,
            duration=event.duration,
            obstructed_breath_count=2,
            recovery_amplitude_increase_pct=0.0,
            confidence=event.confidence if event.confidence is not None else 1.0,
            baseline_flow=event.baseline_flow or 0.0,
        )
        for event in machine_events
        if event.event_type == EVENT_TYPE_RERA
    ]
