"""Rich-based display formatting for CLI output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from snore.analysis.types import AnalysisEvent
from snore.constants import FLOW_LIMITATION_CLASSES
from snore.waveform import format_time_offset

if TYPE_CHECKING:
    from snore.analysis.modes.types import ModeResult
    from snore.analysis.service import AnalysisResult
    from snore.services.schemas import SessionDetail


def create_console(plain: bool = False) -> Console:
    """
    Create a Console instance with appropriate settings.

    Args:
        plain: If True, disable colors and formatting

    Returns:
        Configured Console instance
    """
    if plain:
        return Console(
            force_terminal=False,
            no_color=True,
            legacy_windows=False,
            force_interactive=False,
        )
    return Console()


def _get_box_style(plain: bool = False) -> box.Box:
    """Get the appropriate box style for tables/panels."""
    if plain:
        return box.ASCII
    return box.ROUNDED


def create_header_panel(
    session_date: str, duration: float, plain: bool = False
) -> Panel:
    """
    Create the analysis header panel.

    Args:
        session_date: Session date string
        duration: Session duration in hours

    Returns:
        Rich Panel with header information
    """
    content = Text()
    content.append(f"Session: {session_date}                  ", style="bold")
    content.append(f"Duration: {duration:.1f} hours\n", style="bold")
    content.append(
        "Legend: OA=Obstructive  CA=Central  MA=Mixed  H=Hypopnea",
        style="dim",
    )

    return Panel(
        content,
        title="[bold cyan]ANALYSIS SUMMARY[/bold cyan]"
        if not plain
        else "ANALYSIS SUMMARY",
        border_style="cyan" if not plain else "none",
        padding=(1, 2),
        box=_get_box_style(plain),
    )


def create_machine_events_table(
    machine_events: list[AnalysisEvent],
    session_duration_hours: float,
    plain: bool = False,
) -> Table:
    """
    Create table for machine-detected events.

    Args:
        machine_events: List of machine-detected events
        session_duration_hours: Session duration in hours

    Returns:
        Rich Table with machine event statistics
    """
    from snore.constants import (
        EVENT_TYPE_CENTRAL_APNEA,
        EVENT_TYPE_CLEAR_AIRWAY,
        EVENT_TYPE_HYPOPNEA,
        EVENT_TYPE_MIXED_APNEA,
        EVENT_TYPE_OBSTRUCTIVE_APNEA,
    )

    machine_event_counts: dict[str, int] = {}
    for event in machine_events:
        machine_event_counts[event.event_type] = (
            machine_event_counts.get(event.event_type, 0) + 1
        )

    oa_count = machine_event_counts.get(EVENT_TYPE_OBSTRUCTIVE_APNEA, 0)
    ca_count = machine_event_counts.get(EVENT_TYPE_CENTRAL_APNEA, 0)
    caa_count = machine_event_counts.get(EVENT_TYPE_CLEAR_AIRWAY, 0)
    ma_count = machine_event_counts.get(EVENT_TYPE_MIXED_APNEA, 0)
    h_count = machine_event_counts.get(EVENT_TYPE_HYPOPNEA, 0)

    machine_ahi_count = oa_count + ca_count + caa_count + ma_count + h_count
    machine_ahi = machine_ahi_count / session_duration_hours
    machine_rdi = machine_ahi  # RDI == AHI for CPAP data — RERAs require EEG (in-lab polysomnography only)

    table = Table(
        title="[bold]MACHINE-DETECTED EVENTS (CPAP)[/bold]"
        if not plain
        else "MACHINE-DETECTED EVENTS (CPAP)",
        show_header=False,
        box=_get_box_style(plain) if plain else None,
        padding=(0, 2),
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="bold")

    ahi_color = _get_ahi_color(machine_ahi)
    table.add_row("AHI", f"[{ahi_color}]{machine_ahi:.1f}[/{ahi_color}] events/hr")
    table.add_row("RDI", f"{machine_rdi:.1f} events/hr")
    table.add_section()

    if oa_count > 0:
        table.add_row("Obstructive Apneas (OA)", str(oa_count))
    if caa_count > 0 or ca_count > 0:
        clear_airway_total = caa_count + ca_count
        table.add_row("Central Apneas (CA)", str(clear_airway_total))
    if ma_count > 0:
        table.add_row("Mixed Apneas (MA)", str(ma_count))
    if h_count > 0:
        table.add_row("Hypopneas (H)", str(h_count))

    table.add_section()
    table.add_row("Total Events", str(len(machine_events)))

    return table


def create_mode_comparison_table(
    mode_results: dict[str, Any], plain: bool = False
) -> Table:
    """
    Create comparison table across detection modes.

    Args:
        mode_results: Dictionary of mode results

    Returns:
        Rich Table comparing metrics across modes
    """
    table = Table(
        title="[bold]MODE COMPARISON[/bold]" if not plain else "MODE COMPARISON",
        show_header=True,
        header_style="bold cyan" if not plain else "bold",
        box=_get_box_style(plain) if plain else None,
    )

    table.add_column("Metric", style="cyan")
    for mode_name in mode_results.keys():
        table.add_column(mode_name, justify="right")

    ahi_values = [mode_results[mode].ahi for mode in mode_results]
    ahi_row = ["AHI"]
    for _mode_name, ahi in zip(mode_results.keys(), ahi_values, strict=False):
        color = _get_ahi_color(ahi)
        ahi_row.append(f"[{color}]{ahi:.1f}[/{color}]")
    table.add_row(*ahi_row)

    rdi_values = [f"{mode_results[mode].rdi:.1f}" for mode in mode_results]
    table.add_row("RDI", *rdi_values)

    total_events = [
        str(len(mode_results[mode].apneas) + len(mode_results[mode].hypopneas))
        for mode in mode_results
    ]
    table.add_row("Total Events", *total_events)

    table.add_section()

    apnea_counts = [str(len(mode_results[mode].apneas)) for mode in mode_results]
    table.add_row("Apneas", *apnea_counts)

    for apnea_type in ["OA", "CA", "MA", "UA"]:
        type_counts = []
        has_any = False
        for mode in mode_results:
            count = sum(
                1 for a in mode_results[mode].apneas if a.event_type == apnea_type
            )
            type_counts.append(str(count) if count > 0 else "0")
            if count > 0:
                has_any = True
        if has_any:
            table.add_row(f"  └─ {apnea_type}", *type_counts, style="dim")

    hypopnea_counts = [str(len(mode_results[mode].hypopneas)) for mode in mode_results]
    table.add_row("Hypopneas", *hypopnea_counts)

    return table


def create_validation_table(
    mode_name: str,
    apnea_val: Any,
    hypopnea_val: Any,
    machine_events: list[AnalysisEvent],
    plain: bool = False,
) -> Table:
    """
    Create validation metrics table for a mode.

    Args:
        mode_name: Detection mode name
        apnea_val: Apnea validation results
        hypopnea_val: Hypopnea validation results
        machine_events: Machine-detected events

    Returns:
        Rich Table with validation metrics
    """
    table = Table(
        title=f"[bold]Validation: {mode_name}[/bold]"
        if not plain
        else f"Validation: {mode_name}",
        show_header=True,
        header_style="bold cyan" if not plain else "bold",
        box=_get_box_style(plain) if plain else None,
    )

    table.add_column("Type", style="cyan")
    table.add_column("Sensitivity", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("F1 Score", justify="right")

    if apnea_val.machine_event_count > 0 or apnea_val.programmatic_event_count > 0:
        sens_str = (
            f"{apnea_val.sensitivity * 100:.0f}% "
            f"({apnea_val.matched_events}/{apnea_val.machine_event_count})"
        )
        prec_str = (
            f"{apnea_val.precision * 100:.0f}% "
            f"({apnea_val.matched_events}/{apnea_val.programmatic_event_count})"
        )
        f1_str = f"{apnea_val.f1_score:.2f}"
        table.add_row("Apneas", sens_str, prec_str, f1_str)

    if (
        hypopnea_val.machine_event_count > 0
        or hypopnea_val.programmatic_event_count > 0
    ):
        sens_str = (
            f"{hypopnea_val.sensitivity * 100:.0f}% "
            f"({hypopnea_val.matched_events}/{hypopnea_val.machine_event_count})"
        )
        prec_str = (
            f"{hypopnea_val.precision * 100:.0f}% "
            f"({hypopnea_val.matched_events}/{hypopnea_val.programmatic_event_count})"
        )
        f1_str = f"{hypopnea_val.f1_score:.2f}"
        table.add_row("Hypopneas", sens_str, prec_str, f1_str)

    return table


def format_event_list(
    events: list[Any],
    label: str,
    format_time_fn: Any,
) -> Text:
    """
    Format event list for display.

    Args:
        events: List of events
        label: Label for the list (e.g., "Missed events")
        format_time_fn: Function to format time offsets

    Returns:
        Rich Text with formatted event list
    """
    from snore.analysis.types import AnalysisEvent
    from snore.constants import abbreviate_event_type

    if not events:
        return Text("")

    event_strs = []
    for event in events:
        if isinstance(event, AnalysisEvent):
            time_offset = event.start_time
            event_abbr = abbreviate_event_type(event.event_type)
        elif hasattr(event, "event_type"):
            time_offset = event.start_time
            event_abbr = event.event_type
        else:
            time_offset = event.start_time
            event_abbr = "H"

        event_strs.append(f"{format_time_fn(time_offset)} ({event_abbr})")

    text = Text()
    text.append(f"{label}: ", style="dim")
    text.append(", ".join(event_strs))
    return text


def create_flow_limitation_panel(
    flow_analysis: dict[str, Any], plain: bool = False
) -> tuple[Panel, Table]:
    """
    Create flow limitation analysis panel with embedded table.

    Args:
        flow_analysis: Flow limitation analysis data

    Returns:
        Tuple of (Panel with summary, Table with class distribution)
    """
    fli = flow_analysis["flow_limitation_index"]
    total_breaths = flow_analysis["total_breaths"]

    content = Text()
    content.append(f"Flow Limitation Index: {fli:.2f}       ", style="bold")
    content.append(f"Total Breaths: {total_breaths:,}", style="bold")

    panel = Panel(
        content,
        title="[bold cyan]FLOW LIMITATION ANALYSIS[/bold cyan]"
        if not plain
        else "FLOW LIMITATION ANALYSIS",
        border_style="cyan" if not plain else "none",
        padding=(1, 2),
        box=_get_box_style(plain),
    )

    table = Table(
        show_header=True,
        header_style="bold cyan" if not plain else "bold",
        box=_get_box_style(plain) if plain else None,
    )
    table.add_column("Class", justify="center", style="cyan")
    table.add_column("Name")
    table.add_column("Count", justify="right")
    table.add_column("%", justify="right")
    table.add_column("Severity")

    class_distribution = flow_analysis["class_distribution"]

    for class_num in range(1, 8):
        class_info = FLOW_LIMITATION_CLASSES[class_num]
        count = class_distribution.get(class_num, 0) or class_distribution.get(
            str(class_num), 0
        )
        percentage = (count / total_breaths * 100) if total_breaths > 0 else 0.0

        severity = class_info["severity"]
        severity_color = _get_severity_color(severity)

        table.add_row(
            str(class_num),
            class_info["name"],
            f"{count:,}",
            f"{percentage:.1f}%",
            f"[{severity_color}]{severity}[/{severity_color}]",
        )

    return panel, table


def _get_ahi_color(ahi: float) -> str:
    """Get color for AHI value based on severity."""
    if ahi < 5:
        return "green"
    elif ahi < 15:
        return "yellow"
    elif ahi < 30:
        return "orange"
    else:
        return "red"


def _get_severity_color(severity: str) -> str:
    """Get color for severity level."""
    if "normal" in severity:
        return "green"
    elif "mild" in severity:
        return "yellow"
    elif "moderate" in severity:
        return "orange"
    else:
        return "red"


def display_session_detail(detail: SessionDetail, show_settings: bool) -> None:
    """Print a full session detail view to stdout using click.echo."""
    import pint

    click.echo(f"\nSession ID: {detail.id}")
    click.echo(f"  Device Session ID: {detail.device_session_id}")

    if detail.device_manufacturer and detail.device_model:
        click.echo(
            f"  Device: {detail.device_manufacturer} {detail.device_model} (SN: {detail.device_serial})"
        )

    click.echo(f"  Start: {detail.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    click.echo(f"  End: {detail.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    click.echo(f"  Duration: {detail.duration_hours:.2f}h ({detail.duration_seconds}s)")

    if detail.therapy_mode:
        click.echo(f"  Therapy Mode: {detail.therapy_mode}")

    click.echo("\n  Data:")
    click.echo(f"    Events: {detail.event_count}")
    click.echo(f"    Waveforms: {detail.waveform_count}")
    if detail.waveform_types:
        click.echo(f"    Available types: {', '.join(sorted(detail.waveform_types))}")
    click.echo(f"    Has Statistics: {detail.has_statistics}")
    click.echo(f"    Has Event Data: {detail.has_event_data}")

    stats = detail.statistics
    if stats:
        click.echo("\n  Statistics:")

        if stats.usage_hours is not None:
            click.echo(f"    Usage: {stats.usage_hours:.1f}h")

        has_event_indices = any(
            [
                stats.ahi is not None,
                stats.rei is not None,
                stats.oai is not None,
                stats.cai is not None,
                stats.hi is not None,
            ]
        )
        if has_event_indices:
            click.echo("\n    Event Indices:")
            if stats.ahi is not None:
                click.echo(f"      AHI: {stats.ahi:.1f}")
            if stats.rei is not None:
                click.echo(f"      REI: {stats.rei:.1f}")
            if stats.oai is not None:
                click.echo(f"      OAI: {stats.oai:.1f}")
            if stats.cai is not None:
                click.echo(f"      CAI: {stats.cai:.1f}")
            if stats.hi is not None:
                click.echo(f"      HI: {stats.hi:.1f}")

        has_event_counts = any(
            [
                (stats.obstructive_apneas or 0) > 0,
                (stats.central_apneas or 0) > 0,
                (stats.mixed_apneas or 0) > 0,
                (stats.hypopneas or 0) > 0,
                (stats.reras or 0) > 0,
                (stats.flow_limitations or 0) > 0,
            ]
        )
        if has_event_counts:
            click.echo("\n    Event Counts:")
            if stats.obstructive_apneas and stats.obstructive_apneas > 0:
                click.echo(f"      Obstructive Apneas: {stats.obstructive_apneas}")
            if stats.central_apneas and stats.central_apneas > 0:
                click.echo(f"      Central Apneas: {stats.central_apneas}")
            if stats.mixed_apneas and stats.mixed_apneas > 0:
                click.echo(f"      Mixed Apneas: {stats.mixed_apneas}")
            if stats.hypopneas and stats.hypopneas > 0:
                click.echo(f"      Hypopneas: {stats.hypopneas}")
            if stats.reras and stats.reras > 0:
                click.echo(f"      RERAs: {stats.reras}")
            if stats.flow_limitations and stats.flow_limitations > 0:
                click.echo(f"      Flow Limitations: {stats.flow_limitations}")

        has_pressure = any(
            [
                stats.pressure_mean is not None,
                stats.pressure_min is not None,
                stats.pressure_max is not None,
                stats.pressure_95th is not None,
            ]
        )
        if has_pressure:
            click.echo("\n    Pressure:")
            if stats.pressure_mean is not None:
                click.echo(f"      Mean: {stats.pressure_mean:.1f} cmH₂O")
            if stats.pressure_min is not None and stats.pressure_max is not None:
                click.echo(
                    f"      Range: {stats.pressure_min:.1f} - {stats.pressure_max:.1f} cmH₂O"
                )
            if stats.pressure_95th is not None:
                click.echo(f"      95th percentile: {stats.pressure_95th:.1f} cmH₂O")

        has_epap = any(
            [
                stats.epap_mean is not None,
                stats.epap_min is not None,
                stats.epap_max is not None,
                stats.epap_95th is not None,
            ]
        )
        if has_epap:
            click.echo("\n    EPAP:")
            if stats.epap_mean is not None:
                click.echo(f"      Mean: {stats.epap_mean:.1f} cmH₂O")
            if stats.epap_min is not None and stats.epap_max is not None:
                click.echo(
                    f"      Range: {stats.epap_min:.1f} - {stats.epap_max:.1f} cmH₂O"
                )
            if stats.epap_95th is not None:
                click.echo(f"      95th percentile: {stats.epap_95th:.1f} cmH₂O")

        has_leak = any(
            [
                stats.leak_mean is not None,
                stats.leak_percentile_70 is not None,
                stats.leak_95th is not None,
            ]
        )
        if has_leak:
            click.echo("\n    Leak:")
            if stats.leak_mean is not None:
                click.echo(f"      Mean: {stats.leak_mean:.1f} L/min")
            if stats.leak_percentile_70 is not None:
                click.echo(
                    f"      70th percentile: {stats.leak_percentile_70:.1f} L/min"
                )
            if stats.leak_95th is not None:
                click.echo(f"      95th percentile: {stats.leak_95th:.1f} L/min")

        has_spo2 = any(
            [
                stats.spo2_mean is not None,
                stats.spo2_min is not None,
                stats.spo2_time_below_90 is not None,
            ]
        )
        if has_spo2:
            click.echo("\n    SpO₂:")
            if stats.spo2_mean is not None:
                click.echo(f"      Mean: {stats.spo2_mean:.1f}%")
            if stats.spo2_min is not None:
                click.echo(f"      Minimum: {stats.spo2_min:.0f}%")
            if stats.spo2_time_below_90 is not None:
                minutes_below_90 = stats.spo2_time_below_90 / 60
                click.echo(f"      Time below 90%: {minutes_below_90:.1f} minutes")

        has_pulse = any(
            [
                stats.pulse_mean is not None,
                stats.pulse_min is not None,
                stats.pulse_max is not None,
            ]
        )
        if has_pulse:
            click.echo("\n    Pulse:")
            if stats.pulse_mean is not None:
                click.echo(f"      Mean: {stats.pulse_mean:.1f} BPM")
            if stats.pulse_min is not None and stats.pulse_max is not None:
                click.echo(
                    f"      Range: {stats.pulse_min:.0f} - {stats.pulse_max:.0f} BPM"
                )

        has_respiratory = any(
            [
                stats.respiratory_rate_mean is not None,
                stats.tidal_volume_mean is not None,
                stats.minute_ventilation_mean is not None,
            ]
        )
        if has_respiratory:
            click.echo("\n    Respiratory:")
            if stats.respiratory_rate_mean is not None:
                click.echo(
                    f"      Mean Respiratory Rate: {stats.respiratory_rate_mean:.1f} breaths/min"
                )
            if stats.tidal_volume_mean is not None:
                click.echo(f"      Mean Tidal Volume: {stats.tidal_volume_mean:.0f} mL")
            if stats.minute_ventilation_mean is not None:
                click.echo(
                    f"      Mean Minute Ventilation: {stats.minute_ventilation_mean:.1f} L/min"
                )

    if detail.settings:
        click.echo("\n  Settings:")
        ureg = pint.get_application_registry()  # type: ignore[no-untyped-call]
        for s in detail.settings:
            if s.key == "tube_temp" and s.value:
                try:
                    temp_c = ureg.Quantity(float(s.value), ureg.degC)
                    temp_f = temp_c.to(ureg.degF)
                    click.echo(f"    {s.key}: {temp_f.magnitude:.1f}°F")
                except (ValueError, TypeError):
                    click.echo(f"    {s.key}: {s.value}")
            else:
                click.echo(f"    {s.key}: {s.value}")
    elif show_settings:
        click.echo("\n  Settings: None recorded")

    click.echo()


def _get_validation_metrics(
    mode_result: ModeResult,
    machine_events: list[AnalysisEvent],
    mode: str,
) -> dict[str, Any]:
    from snore.analysis.modes import AVAILABLE_CONFIGS
    from snore.analysis.modes.config import AASM_CONFIG
    from snore.analysis.modes.detector import EventDetector
    from snore.analysis.shared.types import ApneaEvent, HypopneaEvent
    from snore.analysis.utils import convert_machine_events

    machine_apneas, machine_hypopneas = convert_machine_events(machine_events)

    config = AVAILABLE_CONFIGS.get(mode, AASM_CONFIG)
    detector = EventDetector(config)
    validation = detector.validate_against_machine_events(
        mode_result.apneas,
        mode_result.hypopneas,
        machine_apneas,
        machine_hypopneas,
    )

    false_negatives: list[AnalysisEvent] = []

    for machine_event in machine_events:
        is_matched = False
        machine_relative_time = machine_event.start_time
        all_programmatic = list(mode_result.apneas) + list(mode_result.hypopneas)

        for prog_event in all_programmatic:
            time_diff = abs(prog_event.start_time - machine_relative_time)
            if time_diff <= 5.0:
                is_matched = True
                break

        if not is_matched:
            false_negatives.append(machine_event)

    false_positives: list[ApneaEvent | HypopneaEvent] = []

    for prog_event in list(mode_result.apneas) + list(mode_result.hypopneas):
        is_matched = False

        for machine_event in machine_events:
            machine_relative_time = machine_event.start_time
            time_diff = abs(prog_event.start_time - machine_relative_time)
            if time_diff <= 5.0:
                is_matched = True
                break

        if not is_matched:
            false_positives.append(prog_event)

    return {
        "apnea_validation": validation["apnea_validation"],
        "hypopnea_validation": validation["hypopnea_validation"],
        "false_negatives": false_negatives,
        "false_positives": false_positives,
    }


def display_analysis_result(
    result: AnalysisResult, plain: bool, session_date: str
) -> None:
    """
    Display a full analysis result using Rich formatting.

    Args:
        result: Analysis result to display
        plain: If True, disable colors and formatting
        session_date: Session date string for the header
    """

    con = create_console(plain)
    con.print("✓ Analysis complete\n")

    header = create_header_panel(session_date, result.session_duration_hours, plain)
    con.print(header)
    con.print()

    machine_events = result.machine_events
    if machine_events:
        machine_table = create_machine_events_table(
            machine_events, result.session_duration_hours, plain
        )
        con.print(machine_table)
        con.print()

    if result.mode_results:
        mode_table = create_mode_comparison_table(result.mode_results, plain)
        con.print(mode_table)
        con.print()

    if machine_events and result.mode_results:
        con.print(
            "[bold]VALIDATION vs MACHINE EVENTS[/bold]"
            if not plain
            else "VALIDATION vs MACHINE EVENTS"
        )
        con.print()

        for mode_name, mode_result in result.mode_results.items():
            validation = _get_validation_metrics(mode_result, machine_events, mode_name)

            val_table = create_validation_table(
                mode_name,
                validation["apnea_validation"],
                validation["hypopnea_validation"],
                machine_events,
                plain,
            )
            con.print(val_table)

            if validation["false_negatives"]:
                fn_text = format_event_list(
                    validation["false_negatives"],
                    "  Missed events",
                    format_time_offset,
                )
                con.print(fn_text)

            if validation["false_positives"]:
                fp_text = format_event_list(
                    validation["false_positives"],
                    "  Extra events",
                    format_time_offset,
                )
                con.print(fp_text)

            con.print()

    if result.flow_analysis:
        flow_panel, flow_table = create_flow_limitation_panel(
            result.flow_analysis, plain
        )
        con.print(flow_panel)
        con.print(flow_table)
        con.print()
