"""get_waveform_window / render_window tool adapters.

Architecture (plan §9 in-scope/out-of-scope split):
- DB fetch runs INSIDE the server's scope_provider context: ``fetch_waveform_raw``
  holds the AsyncSession only during the query; the scope closes before CPU work begins.
- ``waveform_response_from_raw`` and ``render_png_from_raw`` are PURE — they call
  ``compute_waveform_window`` (deserialize/slice/LTTB) and the PNG renderer outside
  the DB scope.  No session access occurs after ``fetch_waveform_raw`` returns.

Timestamp contract (A6):
- Session wall-clock anchor (session_start_wall_clock) uses tier-2
  (offset-free ISO 8601 + timezone_status="unknown") for absolute times.
- In-session waveform positions are numeric offsets in seconds (tier-3).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.schemas import WaveformChannelSchema, WaveformWindowResponse
from snore.mcp.tools._service_errors import (
    MAPPED_SERVICE_ERRORS,
    raise_mapped_service_error,
)

if TYPE_CHECKING:
    from snore.services.breath_service import RawWaveformWindow


async def fetch_waveform_raw(
    db_session: AsyncSession,
    therapy_date: date,
    profile_id: int,
    offset_start: float,
    offset_end: float,
    device_id: int | None = None,
    session_id: int | None = None,
    channels: list[str] | None = None,
    max_points: int | None = None,
    *,
    window_cap_seconds: float,
) -> RawWaveformWindow:
    """Resolve and fetch raw waveform blobs within the caller's DB scope.

    Builds a ``WaveformWindowRequest`` (Pydantic coerces channel strings to
    ``WaveformChannelName`` and enforces offset ordering, window cap, and
    max_points bounds), then delegates to ``BreathService.fetch_waveform_window``.

    ``DeviceNotOwnedError``, ``DeviceAmbiguityError``, and
    ``MultiSessionAmbiguityError`` are mapped to ``ValidationError`` via
    ``raise_mapped_service_error``.  ``ValueError`` (e.g. explicit session not
    found on a device) and pydantic ``ValidationError`` from request construction
    propagate unchanged to the server's ``tool_error_boundary``.

    Args:
        db_session: Async database session (used only inside this call).
        therapy_date: Therapy date to query.
        profile_id: Profile scope for all ownership checks.
        offset_start: Window start in seconds from session start (>= 0).
        offset_end: Window end in seconds from session start (> offset_start).
        device_id: Filter to a specific device; required when the profile has
            multiple devices with sessions on the date.
        session_id: Filter to a specific session; required when the device had
            multiple sessions on the date.
        channels: Waveform channel names to fetch (e.g. ["flow", "pressure"]).
            Defaults to [flow, pressure, leak] when empty or None.
        max_points: LTTB target point count for downsampling (1–1000).
        window_cap_seconds: Maximum window width in seconds (enforced by caller).
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        WaveformWindowRequest,
    )

    # Pydantic coerces channel strings to WaveformChannelName and enforces
    # offset ordering, window cap, and max_points bounds. Validation errors
    # propagate to the server's tool_error_boundary → ToolError.
    request = WaveformWindowRequest(
        therapy_date=therapy_date,
        device_id=device_id,
        session_id=session_id,
        offset_start=offset_start,
        offset_end=offset_end,
        channels=channels or [],  # type: ignore[arg-type]  # pydantic coerces str→WaveformChannelName
        max_points=max_points,
        window_cap_seconds=window_cap_seconds,
    )

    bs = BreathService(db_session, profile_id)
    try:
        return await bs.fetch_waveform_window(request)
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)


def waveform_response_from_raw(raw: RawWaveformWindow) -> WaveformWindowResponse:
    """Build a ``WaveformWindowResponse`` from a raw fetch result.

    Pure — no DB access.  Calls ``compute_waveform_window`` (deserialize/slice/LTTB)
    then maps the ``WaveformWindow`` DTO to the MCP response schema.

    Session sentinel scrub: ``session_id == 0`` means no session existed on the
    queried date.  Both ``session_id`` and ``session_start_wall_clock`` are emitted
    as ``null`` in that case — the ``datetime.min`` sentinel is never exposed.
    """
    from snore.services.breath_service import compute_waveform_window  # noqa: PLC0415

    window = compute_waveform_window(raw)

    # Empty-day sentinel: session_id=0 means no session on the date.
    session_id = window.session_id if window.session_id > 0 else None
    session_start_wall_clock = (
        window.session_start_wall_clock.isoformat() if window.session_id > 0 else None
    )

    channels = [
        WaveformChannelSchema(
            channel_type=str(ch.channel_type),
            unit=ch.unit,
            sample_rate_hz=ch.sample_rate,
            offset_seconds=ch.offset_seconds,
            values=ch.values,
            original_sample_count=ch.original_sample_count,
            is_downsampled=ch.is_downsampled,
        )
        for ch in window.channels
    ]

    return WaveformWindowResponse(
        session_id=session_id,
        session_start_wall_clock=session_start_wall_clock,
        timezone_status=str(window.timezone_status),
        window_start_offset_s=window.window_start_offset,
        window_end_offset_s=window.window_end_offset,
        channels=channels,
        missing_channels=[str(c) for c in window.missing_channels],
        missing_channel_reason=(
            str(window.missing_channel_reason)
            if window.missing_channel_reason
            else None
        ),
    )


def render_png_from_raw(raw: RawWaveformWindow) -> bytes:
    """Render a raw waveform window as a PNG and return the raw bytes.

    Pure — no DB access.  Calls ``compute_waveform_window`` then passes the
    ``WaveformWindow`` DTO to the renderer.  An empty-channel window (e.g. no
    session on the queried date) still produces a valid single-panel PNG with a
    "No waveform data" label.

    Returns:
        Raw PNG bytes; always starts with the PNG magic ``\\x89PNG\\r\\n\\x1a\\n``.
    """
    from snore.mcp.rendering import render_waveform_window  # noqa: PLC0415
    from snore.services.breath_service import compute_waveform_window  # noqa: PLC0415

    window = compute_waveform_window(raw)
    return render_waveform_window(window)
