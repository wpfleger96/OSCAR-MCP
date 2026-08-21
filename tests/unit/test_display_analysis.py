"""Unit tests for cli/display/analysis rendering helpers."""

from io import StringIO

from rich.console import Console
from rich.table import Table

from snore.analysis.shared.types import ApneaEvent, HypopneaEvent, RERAEvent
from snore.cli.display.analysis import create_validation_table, format_event_list
from snore.services.schemas import EventValidationResult


def _render(table: Table) -> str:
    buf = StringIO()
    Console(file=buf, width=200, no_color=True, force_terminal=False).print(table)
    return buf.getvalue()


def _val(
    machine: int,
    programmatic: int,
    matched: int,
    sensitivity: float,
    precision: float,
    f1: float,
) -> EventValidationResult:
    return EventValidationResult(
        machine_event_count=machine,
        programmatic_event_count=programmatic,
        matched_events=matched,
        false_positives=programmatic - matched,
        false_negatives=machine - matched,
        sensitivity=sensitivity,
        precision=precision,
        f1_score=f1,
        agreement_percentage=0.0,
    )


def _apnea(start: float) -> ApneaEvent:
    return ApneaEvent(
        start_time=start,
        end_time=start + 10.0,
        duration=10.0,
        event_type="OA",
        flow_reduction=0.9,
        confidence=0.8,
        baseline_flow=50.0,
    )


def _hypopnea(start: float) -> HypopneaEvent:
    return HypopneaEvent(
        start_time=start,
        end_time=start + 10.0,
        duration=10.0,
        flow_reduction=0.5,
        confidence=0.7,
        baseline_flow=50.0,
    )


def _rera(start: float) -> RERAEvent:
    return RERAEvent(
        start_time=start,
        end_time=start + 12.0,
        duration=12.0,
        obstructed_breath_count=3,
        recovery_amplitude_increase_pct=0.6,
        confidence=0.7,
        baseline_flow=50.0,
    )


class TestFormatEventList:
    def test_rera_event_renders_re_label(self):
        """RERAEvent (no event_type attr) renders as 'RE', not the 'H' fallback."""
        text = format_event_list([_rera(100.0)], "Missed", lambda t: f"{int(t)}s")
        assert "(RE)" in text.plain

    def test_apnea_and_hypopnea_labels_unchanged(self):
        """Typed apnea keeps its event_type; hypopnea keeps the 'H' label."""
        text = format_event_list(
            [_apnea(10.0), _hypopnea(30.0)], "Missed", lambda t: f"{int(t)}s"
        )
        assert "(OA)" in text.plain
        assert "(H)" in text.plain
        assert "(RE)" not in text.plain

    def test_empty_list_renders_empty_text(self):
        assert format_event_list([], "Missed", str).plain == ""


class TestCreateValidationTableReraRow:
    def test_machine_reras_present_renders_metric_row(self):
        """status 'ok' with device REs → RERAs row shows sensitivity/precision/F1."""
        table = create_validation_table(
            "aasm",
            _val(0, 0, 0, 1.0, 1.0, 0.0),
            _val(0, 0, 0, 1.0, 1.0, 0.0),
            [],
            rera_val=_val(2, 2, 1, 0.5, 0.5, 0.5),
            rera_status="ok",
        )
        output = _render(table)
        assert "RERAs" in output
        assert "50%" in output
        assert "(1/2)" in output
        assert "no machine RE events" not in output

    def test_programmatic_reras_without_machine_renders_placeholder(self):
        """Prog RERAs but no device REs → explicit placeholder, not a 0% row."""
        table = create_validation_table(
            "aasm",
            _val(0, 0, 0, 1.0, 1.0, 0.0),
            _val(0, 0, 0, 1.0, 1.0, 0.0),
            [],
            rera_val=_val(0, 3, 0, 1.0, 0.0, 0.0),
            rera_status="no_machine_re_events",
        )
        output = _render(table)
        assert "RERAs" in output
        assert "no machine RE events" in output

    def test_no_reras_omits_row(self):
        """No rera_val, and an empty no-RE rera_val, both omit the RERAs row."""
        no_val = create_validation_table(
            "aasm",
            _val(1, 1, 1, 1.0, 1.0, 1.0),
            _val(1, 1, 1, 1.0, 1.0, 1.0),
            [],
        )
        assert "RERAs" not in _render(no_val)

        empty_rera = create_validation_table(
            "aasm",
            _val(1, 1, 1, 1.0, 1.0, 1.0),
            _val(1, 1, 1, 1.0, 1.0, 1.0),
            [],
            rera_val=_val(0, 0, 0, 1.0, 0.0, 0.0),
            rera_status="no_machine_re_events",
        )
        assert "RERAs" not in _render(empty_rera)
