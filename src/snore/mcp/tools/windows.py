"""find_windows tool — BreathService.find_windows() adapter.

Timestamp contract (A6):
- Session wall-clock anchors are stored as naive device datetimes.
- Output uses tier-2 (offset-free ISO 8601 + timezone_status="unknown")
  for session wall-clock anchors and tier-3 (float offsets from session
  start) for in-session window positions.  No UTC offsets are fabricated.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.errors import ValidationError
from snore.mcp.schemas import FindWindowsResponse, SessionCoverageEntry, WindowRow
from snore.mcp.tools._capabilities import build_device_capabilities
from snore.mcp.tools._service_errors import (
    MAPPED_SERVICE_ERRORS,
    raise_mapped_service_error,
)


async def find_windows(
    db_session: AsyncSession,
    therapy_date: date,
    profile_id: int,
    criterion: str,
    n: int = 5,
    device_id: int | None = None,
    include_unknown_leak: bool = False,
    flattening_threshold: float | None = None,
    min_window_breaths: int = 3,
    context_breaths_before: int = 3,
    context_breaths_after: int = 3,
    context_seconds: float = 120.0,
    min_fl_run_length: int = 2,
    fl_class_threshold: int = 4,
) -> FindWindowsResponse:
    """Return N worst breath windows matching a flow-limitation criterion.

    Validates criterion, delegates all ranking/dedup/window construction
    to BreathService.find_windows, then maps the typed DTO to FindWindowsResponse.

    Args:
        db_session: Async database session.
        therapy_date: The therapy date to query.
        profile_id: Profile scope — all ownership checks are in the service.
        criterion: Window selection criterion (one of the three WindowCriterion values).
        n: Number of windows to return (1–50; pre-validated by server wrapper).
        device_id: Optional device filter; required when multiple devices share a date.
        include_unknown_leak: Include breaths with unknown leak validity (leak_valid=None).
        flattening_threshold: Minimum mid-insp flattening to anchor a window.
        min_window_breaths: Minimum breaths per formed window.
        context_breaths_before: Context breaths before the anchor breath.
        context_breaths_after: Context breaths after the anchor breath.
        context_seconds: Window half-width in seconds (ca_centered only).
        min_fl_run_length: Minimum FL-class run length (fl_run_ending_in_recovery only).
        fl_class_threshold: Minimum flow_class value to count as flow-limited.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        WindowCriterion,
        WindowCriterionOptions,
    )

    # Validate criterion before touching the DB; service mock must not be called on error.
    try:
        crit = WindowCriterion(criterion)
    except ValueError:
        raise ValidationError(
            f"Unknown criterion {criterion!r}. Valid criteria: "
            "worst_flattening_leak_valid, ca_centered, fl_run_ending_in_recovery"
        ) from None

    opts = WindowCriterionOptions(
        include_unknown_leak=include_unknown_leak,
        flattening_threshold=flattening_threshold,
        min_window_breaths=min_window_breaths,
        context_breaths_before=context_breaths_before,
        context_breaths_after=context_breaths_after,
        context_seconds=context_seconds,
        min_fl_run_length=min_fl_run_length,
        fl_class_threshold=fl_class_threshold,
    )

    try:
        result = await BreathService(db_session, profile_id).find_windows(
            therapy_date=therapy_date,
            criterion=crit,
            n=n,
            options=opts,
            device_id=device_id,
        )
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)
    # ValueError for criterion-irrelevant options propagates unchanged;
    # the server tool_error_boundary maps it to ToolError.

    # Service uses device_id=0 as a sentinel for "no device resolved" (never emit it).
    resolved_device_id = result.device_id if result.device_id > 0 else None

    coverage = [
        SessionCoverageEntry(
            session_id=c.session_id,
            analysis_status=str(c.analysis_status),
            algo_versions=c.algo_versions.model_dump(mode="json")
            if c.algo_versions is not None
            else None,
        )
        for c in result.session_coverage
    ]

    windows = [
        WindowRow(
            criterion=str(w.criterion),
            session_id=w.session_id,
            session_start_wall_clock=w.session_start_wall_clock.isoformat(),
            timezone_status="unknown",
            window_start_offset=w.window_start_offset,
            window_end_offset=w.window_end_offset,
            reason_summary=w.reason_summary,
            worst_mid_insp_flattening=w.worst_mid_insp_flattening,
            fl_run_length=w.fl_run_length,
            anchor_event_offset=w.anchor_event_offset,
            analysis_result_id=w.analysis_result_id,
            analysis_status=str(w.analysis_status),
            analysis_reason=str(w.analysis_reason)
            if w.analysis_reason is not None
            else None,
        )
        for w in result.windows
    ]

    caps = None
    if resolved_device_id is not None:
        caps = await build_device_capabilities(
            db_session,
            profile_id,
            resolved_device_id,
            date_start=therapy_date,
            date_end=therapy_date,
        )

    return FindWindowsResponse(
        query_date=therapy_date.isoformat(),
        device_id=resolved_device_id,
        criterion=str(result.criterion),
        day_status=str(result.day_status),
        session_coverage=coverage,
        algorithm_identity=result.algorithm_identity.model_dump(mode="json")
        if result.algorithm_identity is not None
        else None,
        null_reason=str(result.null_reason) if result.null_reason is not None else None,
        primary_mode=result.primary_mode,
        windows=windows,
        device_capabilities=caps,
    )
