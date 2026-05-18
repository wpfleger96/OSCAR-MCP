"""Waveform inspection and visualization commands."""

from __future__ import annotations

import csv

from datetime import datetime
from typing import Any

import click

from snore.cli.decorators import db_option, init_db
from snore.cli.display import console, print_warning
from snore.waveform import format_time_offset
from snore.waveform.inspector import parse_time_offset


def _resolve_session_id(
    db_session: Any,
    session_id: int | None,
    date: datetime | None,
) -> int:
    """
    Resolve session ID from either explicit ID or date.

    Args:
        db_session: Database session
        session_id: Explicit session ID (takes precedence)
        date: Date to look up session

    Returns:
        Resolved session ID

    Raises:
        SystemExit: If session cannot be resolved
    """
    from snore.services.session_service import SessionService

    service = SessionService(db_session)

    try:
        return service.resolve_session_id(session_id, date)
    except ValueError as e:
        raise click.ClickException(str(e)) from e


@click.group()
def waveform() -> None:
    """Waveform inspection and visualization commands."""
    pass


@waveform.command("list")
@click.option("--session-id", type=int, help="Session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Session date (YYYY-MM-DD)",
)
@db_option
def list_waveforms(
    session_id: int | None,
    date: datetime | None,
    db: str | None,
) -> None:
    """
    List available waveform types for a session.

    Shows all waveform data available for the specified session, including
    sample rates, sample counts, units, and durations.

    Examples:
        snore waveform list --session-id 37
        snore waveform list --date 2025-10-25
    """
    from snore.database.session import session_scope
    from snore.services.waveform_service import WaveformService

    if session_id is None and date is None:
        raise click.ClickException("Either --session-id or --date must be provided")

    init_db(db)

    with session_scope() as db_session:
        resolved_id = _resolve_session_id(db_session, session_id, date)

        service = WaveformService(db_session)
        waveforms = service.list_waveforms(resolved_id)

        if not waveforms:
            console.print(f"No waveforms found for session {resolved_id}")
            return

        console.print(f"Available waveforms for session {resolved_id}:")
        console.print(
            f"  {'TYPE':<12} {'RATE':<12} {'SAMPLES':<10} {'UNIT':<10} {'DURATION'}"
        )

        for wf in waveforms:
            unit = wf.unit or "?"
            rate_str = f"{wf.sample_rate:.1f}Hz"

            console.print(
                f"  {wf.waveform_type:<12} "
                f"{rate_str:<12} "
                f"{wf.sample_count:<10} "
                f"{unit:<10} "
                f"{wf.duration_hours:.1f}h"
            )


@waveform.command("show")
@click.option("--session-id", type=int, help="Session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Session date (YYYY-MM-DD)",
)
@click.option("--time", required=True, help="Time offset (HH:MM:SS)")
@click.option(
    "--window", type=int, default=60, help="Window size in seconds (default: 60)"
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["plot", "csv"]),
    default="plot",
    help="Output format (plot=interactive graph, csv=data export)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path (required for csv format)",
)
@db_option
@click.option(
    "--mode", "-m", default="aasm", help="Detection mode to compare (default: aasm)"
)
@click.option(
    "--type",
    "waveform_type",
    default="flow",
    help="Waveform type to display (default: flow)",
)
def show_waveform(
    session_id: int | None,
    date: datetime | None,
    time: str,
    window: int,
    output_format: str,
    output: str | None,
    db: str | None,
    mode: str,
    waveform_type: str,
) -> None:
    """
    Display waveform at a specific time.

    View waveform data centered on a specific time offset to visually
    inspect detected respiratory events (for flow waveforms).

    Examples:
        snore waveform show --session-id 37 --time 05:56:22 --window 30
        snore waveform show --date 2025-10-25 --time 01:25:16 --type pressure
        snore waveform show --session-id 37 --time 01:25:16 --format csv --output waveform.csv
    """
    from snore.analysis.service import AnalysisService
    from snore.database.session import session_scope
    from snore.waveform import WaveformInspector, WaveformRenderer

    if session_id is None and date is None:
        raise click.ClickException("Either --session-id or --date must be provided")

    if output_format == "csv" and output is None:
        raise click.ClickException("--output is required for csv format")

    waveform_types = [t.strip() for t in waveform_type.split(",")]

    if output_format == "csv" and len(waveform_types) > 1:
        raise click.ClickException("CSV export only supports single waveform type")

    if len(waveform_types) > 4:
        raise click.ClickException("Maximum 4 waveform types supported")

    init_db(db)

    try:
        center_seconds = parse_time_offset(time)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    with session_scope() as db_session:
        session_id = _resolve_session_id(db_session, session_id, date)

        inspector = WaveformInspector(db_session)

        if len(waveform_types) == 1:
            waveform_type_single = waveform_types[0]
            try:
                timestamps, values, metadata = inspector.get_window(
                    session_id=session_id,
                    center_seconds=center_seconds,
                    window_seconds=float(window),
                    waveform_type=waveform_type_single,
                )
            except Exception as e:
                raise click.ClickException(f"Error loading waveform: {e}") from e

            if len(timestamps) == 0:
                raise click.ClickException("No data in window")

            machine_events = []
            programmatic_events = []

            if waveform_type_single == "flow":
                analysis_service = AnalysisService(db_session)
                try:
                    result = analysis_service.get_analysis_result(session_id)
                except Exception:
                    result = None

                if result:
                    start_time = center_seconds - window / 2
                    end_time = center_seconds + window / 2

                    if result.machine_events:
                        machine_events = inspector.find_events_in_window(
                            result.machine_events, start_time, end_time
                        )

                    if mode in result.mode_results:
                        mode_result = result.mode_results[mode]
                        all_prog_events = list(mode_result.apneas) + list(
                            mode_result.hypopneas
                        )
                        programmatic_events = inspector.find_events_in_window(
                            all_prog_events, start_time, end_time
                        )

            if output_format == "plot":
                show_events = waveform_type_single == "flow"
                renderer = WaveformRenderer(
                    width=80, height=20, show_events=show_events
                )
                renderer.render(
                    timestamps=timestamps,
                    values=values,
                    machine_events=machine_events,
                    programmatic_events=programmatic_events,
                    session_id=session_id,
                    center_time=time,
                    waveform_type=waveform_type_single,
                )

            elif output_format == "csv":
                assert output is not None
                column_name = f"{waveform_type_single}_value"
                with open(output, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["timestamp_seconds", column_name])
                    for ts, value in zip(timestamps, values, strict=True):
                        writer.writerow([f"{ts:.3f}", f"{value:.3f}"])

                console.print(f"Exported {len(timestamps)} samples to {output}")

        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            import numpy as np

            def load_waveform(
                wf_type: str,
            ) -> tuple[np.ndarray, np.ndarray, str] | None:
                with session_scope() as thread_session:
                    thread_inspector = WaveformInspector(thread_session)
                    ts, vals, _meta = thread_inspector.get_window(
                        session_id=session_id,
                        center_seconds=center_seconds,
                        window_seconds=float(window),
                        waveform_type=wf_type,
                    )
                if len(ts) > 0:
                    return (ts, vals, wf_type)
                return None

            waveform_data = []
            warnings: list[str] = []
            max_workers = min(4, len(waveform_types))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(load_waveform, wf_type): wf_type
                    for wf_type in waveform_types
                }

                for future in as_completed(futures):
                    wf_type = futures[future]
                    try:
                        wf_result = future.result()
                        if wf_result is not None:
                            waveform_data.append(wf_result)
                        else:
                            warnings.append(f"No data for waveform type '{wf_type}'")
                    except Exception as e:
                        warnings.append(
                            f"Failed to load waveform type '{wf_type}': {e}"
                        )

            for w in warnings:
                print_warning(w)

            if not waveform_data:
                raise click.ClickException("No waveform data loaded")

            type_order = {t: i for i, t in enumerate(waveform_types)}
            waveform_data.sort(key=lambda x: type_order.get(x[2], 999))

            renderer = WaveformRenderer(width=80, height=20, show_events=False)
            renderer.render_multi(
                waveform_data=waveform_data,
                session_id=session_id,
                center_time=time,
            )


@waveform.command("compare")
@click.option("--session-id", type=int, help="Session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Session date (YYYY-MM-DD)",
)
@click.option(
    "--mode", "-m", default="aasm", help="Detection mode to compare (default: aasm)"
)
@click.option("--show-unmatched", is_flag=True, help="Only show unmatched events")
@db_option
def compare_events(
    session_id: int | None,
    date: datetime | None,
    mode: str,
    show_unmatched: bool,
    db: str | None,
) -> None:
    """
    Compare machine vs programmatic events with waveform inspection commands.

    Lists false positives and false negatives with commands to inspect each event.

    Examples:
        snore waveform compare --session-id 37 --mode aasm
        snore waveform compare --date 2025-10-25 --mode resmed --show-unmatched
    """
    from snore.analysis.service import AnalysisService
    from snore.analysis.utils import convert_machine_events
    from snore.database.session import session_scope

    if session_id is None and date is None:
        raise click.ClickException("Either --session-id or --date must be provided")

    init_db(db)

    with session_scope() as db_session:
        session_id = _resolve_session_id(db_session, session_id, date)

        analysis_service = AnalysisService(db_session)
        try:
            result = analysis_service.get_analysis_result(session_id)
        except Exception as e:
            raise click.ClickException(f"Error loading analysis: {e}") from e

        if result is None:
            raise click.ClickException(
                f"No analysis results found for session {session_id}"
            )

        if mode not in result.mode_results:
            raise click.ClickException(f"Mode {mode} not found in analysis results")

        mode_result = result.mode_results[mode]

        machine_events = result.machine_events or []
        machine_apneas, machine_hypopneas = convert_machine_events(machine_events)

        prog_apneas = list(mode_result.apneas)
        prog_hypopneas = list(mode_result.hypopneas)

        false_negatives = []
        false_positives_apnea = []
        false_positives_hypopnea = []

        for m_event in machine_apneas + machine_hypopneas:
            machine_relative_time = m_event.start_time
            is_matched = False

            for p_event in prog_apneas + prog_hypopneas:
                if abs(p_event.start_time - machine_relative_time) <= 5.0:
                    is_matched = True
                    break

            if not is_matched:
                false_negatives.append(m_event)

        for p_event in prog_apneas:
            is_matched = False

            for m_event in machine_apneas + machine_hypopneas:
                machine_relative_time = m_event.start_time
                if abs(p_event.start_time - machine_relative_time) <= 5.0:
                    is_matched = True
                    break

            if not is_matched:
                false_positives_apnea.append(p_event)

        for p_event in prog_hypopneas:
            is_matched = False

            for m_event in machine_apneas + machine_hypopneas:
                machine_relative_time = m_event.start_time
                if abs(p_event.start_time - machine_relative_time) <= 5.0:
                    is_matched = True
                    break

            if not is_matched:
                false_positives_hypopnea.append(p_event)

        console.print(f"Session {session_id} - Event Comparison ({mode} mode)")
        console.print(
            f"Machine: {len(machine_events)} events | Programmatic: {len(prog_apneas) + len(prog_hypopneas)} events"
        )
        console.print("")

        if not show_unmatched or len(false_negatives) > 0:
            console.print(
                f"FALSE NEGATIVES (machine events missed by programmatic): {len(false_negatives)}"
            )
            for event in false_negatives:
                time_str = format_time_offset(event.start_time)
                event_type = getattr(event, "event_type", "H")
                console.print(f"  {event_type} at {time_str} ({event.duration:.1f}s)")
                console.print(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )
            console.print("")

        if (
            not show_unmatched
            or len(false_positives_apnea) + len(false_positives_hypopnea) > 0
        ):
            console.print(
                f"FALSE POSITIVES (programmatic events not in machine): {len(false_positives_apnea) + len(false_positives_hypopnea)}"
            )

            for event in false_positives_apnea:
                time_str = format_time_offset(event.start_time)
                event_type = event.event_type
                conf = getattr(event, "confidence", 0)
                flow_red = getattr(event, "flow_reduction", 0)
                console.print(
                    f"  {event_type} at {time_str} (conf: {conf:.2f}, flow_red: {flow_red * 100:.0f}%)"
                )
                console.print(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )

            for event in false_positives_hypopnea:
                time_str = format_time_offset(event.start_time)
                conf = getattr(event, "confidence", 0)
                flow_red = getattr(event, "flow_reduction", 0)
                console.print(
                    f"  H at {time_str} (conf: {conf:.2f}, flow_red: {flow_red * 100:.0f}%)"
                )
                console.print(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )
