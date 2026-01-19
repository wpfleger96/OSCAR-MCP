"""ASCII and high-resolution waveform rendering for terminal display."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

from uniplot import plot as uniplot_plot

if TYPE_CHECKING:
    from snore.analysis.shared.types import ApneaEvent, HypopneaEvent
    from snore.analysis.types import AnalysisEvent

    EventType = AnalysisEvent | ApneaEvent | HypopneaEvent


def _format_time_offset(seconds: float) -> str:
    """Format seconds to HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class AsciiWaveformRenderer:
    """Render flow waveform as ASCII art for terminal display."""

    def __init__(
        self,
        width: int = 80,
        height: int = 20,
        show_events: bool = True,
        tick_count: int = 10,
    ):
        """
        Initialize renderer.

        Args:
            width: Chart width in characters (default: 80)
            height: Chart height in lines (default: 20)
            show_events: Whether to show event annotations (default: True)
            tick_count: Target number of x-axis tick marks (default: 10)
        """
        self.width = width
        self.height = height
        self.show_events = show_events
        self.tick_count = tick_count

    def render(
        self,
        timestamps: np.ndarray,
        flow_values: np.ndarray,
        machine_events: Sequence["EventType"] | None = None,
        programmatic_events: Sequence["EventType"] | None = None,
        session_id: int | None = None,
        center_time: str | None = None,
    ) -> str:
        """
        Generate ASCII representation of waveform.

        Args:
            timestamps: Timestamp array in seconds
            flow_values: Flow value array in L/min
            machine_events: Machine-detected events in window
            programmatic_events: Programmatically-detected events in window
            session_id: Session ID for title
            center_time: Center time for title

        Returns:
            ASCII art string
        """
        if len(timestamps) == 0 or len(flow_values) == 0:
            return "No data in window"

        lines = []

        if session_id is not None:
            window_size = timestamps[-1] - timestamps[0]

            if center_time:
                title = f"Session {session_id} - Flow Waveform at {center_time} (window: {window_size:.0f}s)"
            else:
                title = f"Session {session_id} - Flow Waveform"

            lines.append(title)
            lines.append(
                f"Sample rate: {len(timestamps) / window_size:.0f}Hz | Samples: {len(timestamps)}"
            )
            lines.append("")

        y_label_width = 6

        lines.append("  Flow")
        lines.append("(L/min)")
        chart_width = self.width - y_label_width - 1

        bucket_size = max(1, len(flow_values) // chart_width)
        buckets = []
        for i in range(chart_width):
            start_idx = i * bucket_size
            end_idx = min((i + 1) * bucket_size, len(flow_values))
            if start_idx < len(flow_values):
                bucket_values = flow_values[start_idx:end_idx]
                buckets.append(
                    (float(np.min(bucket_values)), float(np.max(bucket_values)))
                )
            else:
                break

        min_flow = min(bucket_min for bucket_min, _ in buckets)
        max_flow = max(bucket_max for _, bucket_max in buckets)
        flow_range = max_flow - min_flow if max_flow != min_flow else 1.0

        for row in range(self.height):
            row_flow = max_flow - (row / (self.height - 1)) * flow_range

            line = f"{row_flow:>5.0f} │"

            for _col, (bucket_min, bucket_max) in enumerate(buckets):
                min_normalized = (bucket_min - min_flow) / flow_range
                max_normalized = (bucket_max - min_flow) / flow_range

                min_row = int((1 - max_normalized) * (self.height - 1))
                max_row = int((1 - min_normalized) * (self.height - 1))

                if min_row <= row <= max_row:
                    line += "│"
                elif row == self.height // 2 and min_flow < 0 < max_flow:
                    line += "─"
                else:
                    line += " "

            lines.append(line)

        window_duration = timestamps[-1] - timestamps[0]
        start_time = timestamps[0]
        end_time = timestamps[-1]

        target_ticks = self.tick_count
        raw_interval = window_duration / target_ticks
        if raw_interval <= 5:
            tick_interval = 5
        elif raw_interval <= 10:
            tick_interval = 10
        elif raw_interval <= 15:
            tick_interval = 15
        elif raw_interval <= 30:
            tick_interval = 30
        elif raw_interval <= 60:
            tick_interval = 60
        elif raw_interval <= 120:
            tick_interval = 120
        else:
            tick_interval = 300

        first_tick = (int(start_time / tick_interval) + 1) * tick_interval
        ticks = []
        tick_time = first_tick
        while tick_time < end_time:
            ticks.append(tick_time)
            tick_time += tick_interval

        x_axis_chars = [" "] * (y_label_width + 1 + len(buckets))
        x_axis_chars[y_label_width] = "└"
        for i in range(len(buckets)):
            x_axis_chars[y_label_width + 1 + i] = "─"

        for tick in ticks:
            tick_offset = (tick - start_time) / window_duration
            tick_col = int(tick_offset * len(buckets))
            if 0 < tick_col < len(buckets):
                x_axis_chars[y_label_width + 1 + tick_col] = "┬"

        if len(buckets) > 0:
            x_axis_chars[-1] = "┘"

        lines.append("".join(x_axis_chars))

        start_time_str = _format_time_offset(start_time)
        end_time_str = _format_time_offset(end_time)

        time_line_chars = [" "] * (y_label_width + 1 + len(buckets))

        for i, char in enumerate(start_time_str):
            pos = y_label_width + 1 + i
            if pos < len(time_line_chars):
                time_line_chars[pos] = char

        end_label_start = len(time_line_chars) - len(end_time_str)
        for i, char in enumerate(end_time_str):
            pos = end_label_start + i
            if 0 <= pos < len(time_line_chars):
                time_line_chars[pos] = char

        start_label_end = y_label_width + 1 + len(start_time_str)

        for tick in ticks:
            tick_offset = (tick - start_time) / window_duration
            tick_col = int(tick_offset * len(buckets))

            tick_minutes = int((tick % 3600) // 60)
            start_minutes = int((start_time % 3600) // 60)
            tick_hours = int(tick // 3600)
            start_hours = int(start_time // 3600)

            if tick_minutes == start_minutes and tick_hours == start_hours:
                label = f":{int(tick % 60):02d}"
            elif tick_hours == start_hours:
                label = f":{tick_minutes:02d}:{int(tick % 60):02d}"
            else:
                label = _format_time_offset(tick)

            label_start = y_label_width + 1 + tick_col - len(label) // 2
            label_end = label_start + len(label)

            if label_start > start_label_end and label_end < end_label_start - 1:
                for i, char in enumerate(label):
                    pos = label_start + i
                    if time_line_chars[pos] == " ":
                        time_line_chars[pos] = char

        lines.append("".join(time_line_chars))

        time_label = "Time (HH:MM:SS)"
        label_indent = y_label_width + 1 + (len(buckets) - len(time_label)) // 2
        lines.append(f"{' ' * label_indent}{time_label}")

        if self.show_events:
            lines.append("")
            lines.append("Events in window:")

            if machine_events and len(machine_events) > 0:
                for event in machine_events:
                    time_str = _format_time_offset(event.start_time)
                    event_type = getattr(event, "event_type", "Unknown")
                    lines.append(
                        f"  Machine:      {event_type} at {time_str} ({event.duration:.1f}s)"
                    )
            else:
                lines.append("  Machine:      (none)")

            if programmatic_events and len(programmatic_events) > 0:
                for event in programmatic_events:
                    time_str = _format_time_offset(event.start_time)

                    if hasattr(event, "event_type"):
                        event_type = event.event_type
                    else:
                        event_type = "H"

                    flow_red = getattr(event, "flow_reduction", None)
                    if flow_red is not None:
                        lines.append(
                            f"  Programmatic: {event_type} at {time_str} ({event.duration:.1f}s, {flow_red * 100:.0f}% flow reduction)"
                        )
                    else:
                        lines.append(
                            f"  Programmatic: {event_type} at {time_str} ({event.duration:.1f}s)"
                        )
            else:
                lines.append("  Programmatic: (none)")

        return "\n".join(lines)


class UniplotWaveformRenderer:
    """Render flow waveform using uniplot for high-resolution terminal display."""

    def __init__(
        self,
        width: int = 80,
        height: int = 20,
        show_events: bool = True,
        interactive: bool = False,
    ):
        """
        Initialize renderer.

        Args:
            width: Chart width in characters (default: 80)
            height: Chart height in lines (default: 20)
            show_events: Whether to show event annotations (default: True)
            interactive: Enable interactive zoom/pan mode (default: False)
        """
        self.width = width
        self.height = height
        self.show_events = show_events
        self.interactive = interactive

    def render(
        self,
        timestamps: np.ndarray,
        flow_values: np.ndarray,
        machine_events: Sequence["EventType"] | None = None,
        programmatic_events: Sequence["EventType"] | None = None,
        session_id: int | None = None,
        center_time: str | None = None,
    ) -> None:
        """
        Generate high-resolution waveform visualization using uniplot.

        Args:
            timestamps: Timestamp array in seconds
            flow_values: Flow value array in L/min
            machine_events: Machine-detected events in window
            programmatic_events: Programmatically-detected events in window
            session_id: Session ID for title
            center_time: Center time for title

        Note:
            This method prints directly to stdout and returns None.
        """
        if len(timestamps) == 0 or len(flow_values) == 0:
            print("No data in window")
            return

        if session_id is not None:
            window_size = timestamps[-1] - timestamps[0]
            if center_time:
                title = (
                    f"Session {session_id} - Flow at {center_time} ({window_size:.0f}s)"
                )
            else:
                title = f"Session {session_id} - Flow Waveform"
        else:
            title = "Flow Waveform"

        sample_rate = len(timestamps) / (timestamps[-1] - timestamps[0])
        print(f"Sample rate: {sample_rate:.0f}Hz | Samples: {len(timestamps)}")
        print()

        def x_formatter(val: float) -> str:
            """Format x-axis values as HH:MM:SS."""
            return _format_time_offset(val)

        uniplot_plot(
            xs=timestamps,
            ys=flow_values,
            title=title,
            x_unit="",
            y_unit="L/min",
            width=self.width,
            height=self.height,
            lines=True,
            character_set="braille",
            interactive=self.interactive,
            x_gridlines=[0],
            y_gridlines=[0],
        )

        if self.show_events:
            print()
            print("Events in window:")

            if machine_events and len(machine_events) > 0:
                for event in machine_events:
                    time_str = _format_time_offset(event.start_time)
                    event_type = getattr(event, "event_type", "Unknown")
                    print(
                        f"  Machine:      {event_type} at {time_str} ({event.duration:.1f}s)"
                    )
            else:
                print("  Machine:      (none)")

            if programmatic_events and len(programmatic_events) > 0:
                for event in programmatic_events:
                    time_str = _format_time_offset(event.start_time)

                    if hasattr(event, "event_type"):
                        event_type = event.event_type
                    else:
                        event_type = "H"

                    flow_red = getattr(event, "flow_reduction", None)
                    if flow_red is not None:
                        print(
                            f"  Programmatic: {event_type} at {time_str} ({event.duration:.1f}s, {flow_red * 100:.0f}% flow reduction)"
                        )
                    else:
                        print(
                            f"  Programmatic: {event_type} at {time_str} ({event.duration:.1f}s)"
                        )
            else:
                print("  Programmatic: (none)")
