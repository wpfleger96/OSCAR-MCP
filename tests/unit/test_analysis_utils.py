"""Unit tests for analysis.utils machine-event converters."""

from __future__ import annotations

from snore.analysis.types import AnalysisEvent
from snore.analysis.utils import convert_machine_reras


def _event(event_type: str, start: float, duration: float = 12.0) -> AnalysisEvent:
    return AnalysisEvent(
        event_type=event_type,
        start_time=start,
        duration=duration,
        source="machine",
        confidence=0.9,
        baseline_flow=48.0,
    )


class TestConvertMachineReras:
    def test_extracts_only_re_events(self):
        """Only 'RE' events become RERAEvents; apneas/hypopneas are ignored."""
        events = [
            _event("OA", 10.0),
            _event("RE", 30.0),
            _event("H", 50.0),
            _event("RE", 70.0),
        ]

        reras = convert_machine_reras(events)

        assert len(reras) == 2
        assert [r.start_time for r in reras] == [30.0, 70.0]
        # end_time derives from start + duration; confidence carried through.
        assert reras[0].end_time == 42.0
        assert reras[0].confidence == 0.9

    def test_no_re_events_returns_empty(self):
        reras = convert_machine_reras([_event("OA", 10.0), _event("H", 40.0)])
        assert reras == []

    def test_missing_confidence_and_baseline_use_placeholders(self):
        """Null device fields fall back to schema-valid placeholders."""
        event = AnalysisEvent(
            event_type="RE",
            start_time=5.0,
            duration=10.0,
            source="machine",
        )

        (rera,) = convert_machine_reras([event])

        assert rera.confidence == 1.0
        assert rera.baseline_flow == 0.0
        assert rera.obstructed_breath_count == 2

    def test_zero_confidence_is_preserved_not_rewritten(self):
        """A real 0.0 confidence must survive, not fall back to the 1.0 placeholder."""
        event = AnalysisEvent(
            event_type="RE",
            start_time=5.0,
            duration=10.0,
            source="machine",
            confidence=0.0,
        )

        (rera,) = convert_machine_reras([event])

        assert rera.confidence == 0.0
