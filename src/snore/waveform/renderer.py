"""ASCII and high-resolution waveform rendering for terminal display."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np
import plotext as plt

if TYPE_CHECKING:
    from snore.analysis.shared.types import ApneaEvent, HypopneaEvent
    from snore.analysis.types import AnalysisEvent

    EventType = AnalysisEvent | ApneaEvent | HypopneaEvent


def format_time_offset(seconds: float) -> str:
    """Format seconds to HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class WaveformRenderer:
    """Render flow waveform using plotext for high-resolution terminal display."""

    def __init__(
        self,
        width: int = 80,
        height: int = 20,
        show_events: bool = True,
    ):
        """
        Initialize renderer.

        Args:
            width: Chart width in characters (default: 80)
            height: Chart height in lines (default: 20)
            show_events: Whether to show event annotations (default: True)
        """
        self.width = width
        self.height = height
        self.show_events = show_events

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
        Generate high-resolution waveform visualization using plotext.

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

        plt.clear_figure()
        plt.theme("clear")

        start_time = timestamps[0]
        relative_timestamps = timestamps - start_time
        window_duration = timestamps[-1] - start_time

        plt.plot(relative_timestamps, flow_values, marker="braille")
        plt.title(title)
        plt.ylabel("L/min")

        tick_interval = (
            10 if window_duration <= 60 else (15 if window_duration <= 120 else 30)
        )
        tick_positions = list(range(0, int(window_duration) + 1, tick_interval))
        tick_labels = [format_time_offset(start_time + t) for t in tick_positions]
        plt.xticks(tick_positions, tick_labels)

        plt.plotsize(self.width, self.height)
        plt.show()

        if self.show_events:
            print()
            print("Events in window:")

            if machine_events and len(machine_events) > 0:
                for event in machine_events:
                    time_str = format_time_offset(event.start_time)
                    event_type = getattr(event, "event_type", "Unknown")
                    print(
                        f"  Machine:      {event_type} at {time_str} ({event.duration:.1f}s)"
                    )
            else:
                print("  Machine:      (none)")

            if programmatic_events and len(programmatic_events) > 0:
                for event in programmatic_events:
                    time_str = format_time_offset(event.start_time)

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
