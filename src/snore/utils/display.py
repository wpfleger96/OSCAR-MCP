"""Rich-based display formatting for CLI output."""

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from snore.analysis.types import AnalysisEvent
from snore.constants import FLOW_LIMITATION_CLASSES

console = Console()


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
    machine_rdi = machine_ahi

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
    is_false_negatives: bool = False,
    machine_session_start: float = 0.0,
) -> Text:
    """
    Format false positives/negatives with wrapping.

    Args:
        events: List of events
        label: Label for the list (e.g., "Missed events")
        format_time_fn: Function to format time offsets
        is_false_negatives: True if these are false negatives (machine events)
        machine_session_start: Session start timestamp for machine events

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
