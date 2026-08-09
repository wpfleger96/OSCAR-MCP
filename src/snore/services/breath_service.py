"""BreathService — query layer over the breaths table.

All types in this module are the Appendix-A typed seam definitions (plan v3.8).
PR-B (Duncan) consumes these seams; PR-A (this PR) defines and implements them.

All types live here per Appendix A §13 note ("All types live in
src/snore/services/breath_service.py").
"""

from __future__ import annotations

import math

from collections.abc import Callable, Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.shared.versioning import (
    CROSS_VERSION_REFUSAL_KEYS,
    MV_FALLBACK_ALGO_VERSION,
    AlgorithmIdentity,
    AlgoVersions,
    AnalysisRunMetadata,
    AnalysisStatus,
    DayAnalysisStatus,
    NullReason,
    TimezoneStatus,
)

__all__ = [
    # Enums / shared types
    "TimezoneStatus",
    "NullReason",
    "AnalysisStatus",
    "DayAnalysisStatus",
    "AlgorithmIdentity",
    "AnalysisRunMetadata",
    "AlgoVersions",
    "CROSS_VERSION_REFUSAL_KEYS",
    "TriggerType",
    "CycleType",
    "TriggerCycleApplicability",
    "WaveformChannelName",
    # DTOs
    "SessionCoverage",
    "BreathQueryRange",
    "SessionSummary",
    "MultiSessionAmbiguityError",
    "DeviceAmbiguityError",
    "DeviceNotOwnedError",
    "NoSessionsInRangeError",
    "BreathRow",
    "BreathBin",
    "BreathPage",
    "WindowCriterion",
    "WindowCriterionOptions",
    "WindowResult",
    "FindWindowsResult",
    "EpochRequest",
    "DistributionMetric",
    "DistributionStats",
    "EpochRxViolation",
    "EpochBreathStats",
    "CompareEpochsResult",
    "ContextualEvent",
    "RawWaveformChannel",
    "RawWaveformWindow",
    "WaveformChannel",
    "WaveformWindow",
    "WaveformWindowRequest",
    "NightlyAnalysisSummary",
    "NightlyRangeSummary",
    "DeviceCapabilities",
    "CaDetail",
    "CaAnalysisResult",
    "RawCaEvent",
    "RawCaSessionData",
    "RawCaAnalysis",
    # Functions
    "fetch_waveform_window_raw",
    "compute_waveform_window",
    "compute_ca_analysis",
    "derive_mv_from_flow",
    # Service
    "BreathService",
]


# ---------------------------------------------------------------------------
# Appendix A §1 re-exported from versioning (single source of truth)
# ---------------------------------------------------------------------------


class SessionCoverage(BaseModel):
    """Per-session analysis coverage entry (Appendix A §1)."""

    session_id: int
    analysis_status: AnalysisStatus
    algo_versions: AlgoVersions | None


# ---------------------------------------------------------------------------
# §4 — TriggerType / CycleType / TriggerCycleApplicability
# ---------------------------------------------------------------------------


class TriggerType(StrEnum):
    NORMAL = "normal"
    PREMATURE = "premature"
    DELAYED = "delayed"


class CycleType(StrEnum):
    NORMAL = "normal"
    PREMATURE = "premature"


class TriggerCycleApplicability(StrEnum):
    VALIDATED = "validated"
    UNVALIDATED_DEVICE = "unvalidated_device"


# ---------------------------------------------------------------------------
# §2 — BreathQueryRange + SessionSummary + MultiSessionAmbiguityError
# ---------------------------------------------------------------------------


class SessionSummary(BaseModel):
    session_id: int
    start_wall_clock: datetime  # naive — tier-2 device wall-clock
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    duration_seconds: float


class MultiSessionAmbiguityError(Exception):
    """Raised when session_id is required but not supplied for a multi-session day."""

    def __init__(
        self,
        therapy_date: date,
        device_id: int,
        sessions: list[SessionSummary],
    ) -> None:
        self.therapy_date = therapy_date
        self.device_id = device_id
        self.sessions = sessions
        super().__init__(
            f"Multiple sessions on {therapy_date}: pass session_id to disambiguate"
        )


class DeviceNotOwnedError(Exception):
    """Raised when an explicit device_id is not owned by the requesting profile."""

    def __init__(self, device_id: int, profile_id: int) -> None:
        self.device_id = device_id
        self.profile_id = profile_id
        super().__init__(
            f"device_id={device_id} not found or not owned by profile {profile_id}"
        )


class DeviceAmbiguityError(Exception):
    """Raised when device_id is required but not supplied for a multi-device day.

    Contains only owned device IDs so that cross-profile leakage is impossible.
    """

    def __init__(
        self,
        therapy_date: date,
        profile_id: int,
        owned_device_ids: list[int],
        device_serials: dict[int, str],
    ) -> None:
        self.therapy_date = therapy_date
        self.profile_id = profile_id
        self.owned_device_ids = owned_device_ids
        self.device_serials = device_serials
        super().__init__(
            f"Multiple devices have sessions on {therapy_date}: "
            "pass device_id to disambiguate"
        )


class NoSessionsInRangeError(ValueError):
    """Raised by _resolve_range when no owned sessions exist in the queried date range."""

    def __init__(self, date_start: date, date_end: date) -> None:
        self.date_start = date_start
        self.date_end = date_end
        super().__init__(f"No sessions found in range {date_start} to {date_end}")


class BreathQueryRange(BaseModel):
    """Identifies a contiguous waveform window within a therapy day."""

    therapy_date: date
    device_id: int | None = None
    session_id: int | None = None
    offset_start: float = Field(ge=0.0)
    offset_end: float = Field(gt=0.0)

    # Pagination / binning
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=500, ge=1, le=2000)
    bin_minutes: float | None = Field(default=None, ge=1.0)

    @model_validator(mode="after")
    def validate_window(self) -> BreathQueryRange:
        if not (math.isfinite(self.offset_start) and math.isfinite(self.offset_end)):
            raise ValueError("offset_start and offset_end must be finite")
        if self.offset_end <= self.offset_start:
            raise ValueError("offset_end must be > offset_start")
        window_minutes = (self.offset_end - self.offset_start) / 60
        if self.bin_minutes is None and window_minutes > 15:
            raise ValueError(
                f"Raw window {window_minutes:.1f} min exceeds 15-min cap; "
                "set bin_minutes to aggregate"
            )
        return self


# ---------------------------------------------------------------------------
# §4 — BreathRow
# ---------------------------------------------------------------------------


class BreathRow(BaseModel):
    """One row from the breaths table.

    Nullable fields reflect absent measurements — never coerced to zero or
    a default class.  Consumers must check for ``None`` before using
    timing/amplitude/shape values.
    """

    analysis_result_id: int
    session_id: int
    breath_number: int

    session_start_wall_clock: datetime  # naive — tier-2
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    start_offset_seconds: float
    end_offset_seconds: float

    # Timing (None when the segmenter could not resolve them)
    ti: float | None  # inspiration time s
    te: float | None  # expiration time s
    ttot: float | None  # total time s
    ie_ratio: float | None
    duty_cycle: float | None

    # Amplitude (None when channel absent or computation failed)
    peak_insp_flow: float | None  # L/min
    peak_exp_flow: float | None  # L/min
    tidal_volume: float | None  # mL

    # Flow limitation features (None when channel absent)
    flatness_index: float | None
    mid_insp_flattening: float | None

    # Classification (None when not computed)
    flow_class: int | None
    flow_class_confidence: float | None
    is_recovery_breath: bool | None

    # Trigger/cycle heuristic (experimental)
    trigger_type: TriggerType | None
    cycle_type: CycleType | None
    trigger_cycle_confidence: float | None
    trigger_cycle_experimental: Literal[True] = True
    trigger_cycle_applicability: TriggerCycleApplicability | None
    trigger_cycle_reason: NullReason | None

    # Quality flags
    leak_valid: bool | None
    leak_valid_reason: NullReason | None
    ramp_active: bool | None
    ramp_active_reason: NullReason | None
    mask_off: bool | None
    mask_off_reason: NullReason | None


# ---------------------------------------------------------------------------
# §5 — BreathPage
# ---------------------------------------------------------------------------


class BreathBin(BaseModel):
    """Aggregated metrics for one time bin."""

    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    bin_start_offset: float
    bin_end_offset: float
    breath_count: int
    flatness_index_median: float | None
    mid_insp_flattening_median: float | None
    flow_class_mode: int | None
    tidal_volume_median: float | None
    ie_ratio_median: float | None
    leak_valid_fraction: float | None
    analysis_status: AnalysisStatus


class BreathPage(BaseModel):
    """Result of get_breath_table()."""

    query: BreathQueryRange
    analysis_status: AnalysisStatus
    algo_versions: AlgoVersions | None
    null_reason: NullReason | None
    is_binned: bool
    total_breaths: int
    page: int
    page_size: int
    rows: list[BreathRow] = Field(default_factory=list)
    bins: list[BreathBin] = Field(default_factory=list)
    session_id: int | None = None
    """Resolved session for this page; None only on legacy constructions."""


# ---------------------------------------------------------------------------
# §6 — WindowCriterion / WindowCriterionOptions / FindWindowsResult
# ---------------------------------------------------------------------------


class WindowCriterion(StrEnum):
    WORST_FLATTENING_LEAK_VALID = "worst_flattening_leak_valid"
    CA_CENTERED = "ca_centered"
    FL_RUN_ENDING_IN_RECOVERY = "fl_run_ending_in_recovery"


class WindowCriterionOptions(BaseModel):
    """Criterion-specific options."""

    include_unknown_leak: bool = False
    flattening_threshold: float | None = None
    min_window_breaths: int = 3
    context_breaths_before: int = Field(default=3, ge=0)
    context_breaths_after: int = Field(default=3, ge=0)
    context_seconds: float = 120.0
    min_fl_run_length: int = 2
    fl_class_threshold: int = 4


class WindowResult(BaseModel):
    """One found window."""

    criterion: WindowCriterion
    session_id: int
    session_start_wall_clock: datetime
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    window_start_offset: float
    window_end_offset: float
    reason_summary: str
    worst_mid_insp_flattening: float | None
    fl_run_length: int | None
    anchor_event_offset: float | None
    analysis_result_id: int | None
    analysis_status: AnalysisStatus
    analysis_reason: NullReason | None


class FindWindowsResult(BaseModel):
    """Result of find_windows()."""

    query_date: date
    device_id: int
    criterion: WindowCriterion
    day_status: DayAnalysisStatus
    session_coverage: list[SessionCoverage] = Field(default_factory=list)
    algorithm_identity: AlgorithmIdentity | None
    null_reason: NullReason | None
    primary_mode: str | None
    windows: list[WindowResult]


# ---------------------------------------------------------------------------
# §7 — Epoch DTOs
# ---------------------------------------------------------------------------


class EpochRequest(BaseModel):
    """One settings epoch for comparison."""

    label: str
    date_start: date
    date_end: date
    device_id: int | None = None


class DistributionMetric(StrEnum):
    MID_INSP_FLATTENING = "mid_insp_flattening"
    FLATNESS_INDEX = "flatness_index"
    TIDAL_VOLUME_ML = "tidal_volume_ml"
    IE_RATIO = "ie_ratio"


class DistributionStats(BaseModel):
    """Descriptive stats for one metric over one epoch."""

    median: float | None
    iqr: float | None
    p95: float | None
    n_breaths: int
    n_nights: int


class EpochRxViolation(BaseModel):
    epoch_label: str
    changed_keys: list[str]
    change_dates: list[date]


class EpochBreathStats(BaseModel):
    """Breath-feature distributions for one epoch."""

    label: str
    date_start: date
    date_end: date
    nights_with_data: int
    nights_missing_analysis: int
    algorithm_identity: AlgorithmIdentity | None
    null_reason: NullReason | None
    primary_mode: str | None
    mid_insp_flattening: DistributionStats
    flatness_index: DistributionStats
    flow_class_distribution: dict[int, int]
    tidal_volume_ml: DistributionStats
    ie_ratio: DistributionStats
    rera_proxy_count: int | None
    rera_reason: NullReason | None
    rx_settings: dict[str, str]


class CompareEpochsResult(BaseModel):
    """Result of compare_epochs()."""

    epochs: list[EpochBreathStats]
    null_reason: NullReason | None
    rx_violations: list[EpochRxViolation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §8 — ContextualEvent
# ---------------------------------------------------------------------------


class ContextualEvent(BaseModel):
    """One machine-flagged event with surrounding context."""

    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2
    event_type: str
    event_start_wall_clock: datetime  # naive — tier-2
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    offset_seconds: float
    duration_seconds: float | None

    pressure_at_event_cmh2o: float | None
    pressure_reason: NullReason | None
    leak_at_event_lpm: float | None
    leak_reason: NullReason | None
    mv_prior_120s_lpm: float | None
    mv_reason: NullReason | None
    minutes_since_session_start: float


# ---------------------------------------------------------------------------
# §9 — Multi-channel waveform-window DTOs + split seam functions
# ---------------------------------------------------------------------------


class WaveformChannelName(StrEnum):
    """Typed channel names (persisted Waveform.waveform_type values)."""

    FLOW = "flow"
    PRESSURE = "pressure"
    THERAPY_PRESSURE = "therapy_pressure"
    EPAP = "epap"
    LEAK = "leak"
    MV = "mv"
    RR = "rr"
    TV = "tv"
    SPO2 = "spo2"
    PULSE = "pulse"
    FL = "fl"
    SNORE = "snore"


class RawWaveformChannel(BaseModel):
    waveform_type: WaveformChannelName
    unit: str | None
    sample_rate: float
    sample_count: int
    raw_bytes: bytes


class RawWaveformWindow(BaseModel):
    request: WaveformWindowRequest
    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    channels: list[RawWaveformChannel]
    missing_channels: list[WaveformChannelName]


class WaveformChannel(BaseModel):
    """One deserialized, windowed waveform channel."""

    channel_type: WaveformChannelName
    unit: str | None
    sample_rate: float
    offset_seconds: list[float]
    values: list[float]
    original_sample_count: int
    is_downsampled: bool


class WaveformWindow(BaseModel):
    """Multi-channel waveform data for a time window."""

    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    window_start_offset: float
    window_end_offset: float
    channels: list[WaveformChannel]
    missing_channels: list[WaveformChannelName]
    missing_channel_reason: NullReason | None


class WaveformWindowRequest(BaseModel):
    """Request for a multi-channel waveform window."""

    therapy_date: date
    device_id: int | None = None
    session_id: int | None = None
    offset_start: float = Field(ge=0.0)
    offset_end: float = Field(gt=0.0)
    channels: list[WaveformChannelName] = Field(
        default_factory=list,
        max_length=len(WaveformChannelName),
    )
    max_points: int | None = Field(default=None, ge=1, le=1000)
    window_cap_seconds: float = Field(default=120.0, gt=0.0)

    @model_validator(mode="after")
    def validate_request(self) -> WaveformWindowRequest:
        if self.offset_end <= self.offset_start:
            raise ValueError("offset_end must be > offset_start")
        window_seconds = self.offset_end - self.offset_start
        if window_seconds > self.window_cap_seconds:
            raise ValueError(
                f"Window {window_seconds:.0f} s exceeds the {self.window_cap_seconds:.0f} s "
                "cap for this tool; narrow the window"
            )
        if not self.channels:
            self.channels = [
                WaveformChannelName.FLOW,
                WaveformChannelName.PRESSURE,
                WaveformChannelName.LEAK,
            ]
        # Dedup, order-preserving
        self.channels = list(dict.fromkeys(self.channels))
        return self


def _extract_window_mean(
    offsets: list[float],
    values: list[float],
    offset_start: float,
    offset_end: float,
) -> float | None:
    """Mean of values whose offset falls in [offset_start, offset_end]. None if empty."""
    slice_vals = [
        v
        for o, v in zip(offsets, values, strict=True)
        if offset_start <= o <= offset_end
    ]
    return sum(slice_vals) / len(slice_vals) if slice_vals else None


async def _fetch_waveform_blobs(
    db: AsyncSession,
    request: WaveformWindowRequest,
    session_id: int,
    session_start: datetime,
) -> RawWaveformWindow:
    """PRIVATE — fetch waveform blobs for a pre-resolved, already-owned session.

    Trusted internal helper: ownership has already been verified by the caller
    (via ``_resolve_range`` or ``fetch_waveform_window_raw``).  No ownership
    check or Session query is performed here.
    """

    from sqlalchemy import select  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415

    requested_types = [ch.value for ch in request.channels]
    wf_stmt = select(models.Waveform).where(
        models.Waveform.session_id == session_id,
        models.Waveform.waveform_type.in_(requested_types),
    )
    wf_rows = (await db.execute(wf_stmt)).scalars().all()
    wf_by_type = {w.waveform_type: w for w in wf_rows}

    channels: list[RawWaveformChannel] = []
    missing: list[WaveformChannelName] = []
    for ch in request.channels:
        wf = wf_by_type.get(ch.value)
        if wf is None:
            missing.append(ch)
        else:
            channels.append(
                RawWaveformChannel(
                    waveform_type=ch,
                    unit=getattr(wf, "unit", None),
                    sample_rate=wf.sample_rate or 1.0,
                    sample_count=getattr(wf, "sample_count", 0),
                    raw_bytes=wf.data_blob or b"",
                )
            )

    return RawWaveformWindow(
        request=request,
        session_id=session_id,
        session_start_wall_clock=session_start,
        channels=channels,
        missing_channels=missing,
    )


async def fetch_waveform_window_raw(
    db: AsyncSession,
    profile_id: int,
    request: WaveformWindowRequest,
) -> RawWaveformWindow:
    """PUBLIC — fetch waveform blobs with profile-level ownership enforcement.

    Never closes db: the scope owner opens and closes the scope around this call.

    ``request.session_id`` must be set (direct callers must have a resolved session).
    Verifies ``Device.profile_id == profile_id`` via a join; raises ``ValueError``
    when the session is not found or is not owned by ``profile_id``.  Derives
    ``session_start`` from the DB row — never from caller-supplied data (plan §9
    lines 720-735).
    """

    from sqlalchemy import select  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415

    if request.session_id is None:
        raise ValueError(
            "request.session_id must be set; direct callers of fetch_waveform_window_raw "
            "must resolve a session before calling this function"
        )

    # Full-tuple ownership query: Session + Device (profile) + Day (date) + optional device.
    # plan §9 lines 822-825: the session must match profile_id, therapy_date, AND device_id.
    stmt = (
        select(models.Session.start_time)
        .join(models.Device, models.Session.device_id == models.Device.id)
        .join(models.Day, models.Session.day_id == models.Day.id)
        .where(
            models.Session.id == request.session_id,
            models.Device.profile_id == profile_id,
            models.Day.date == request.therapy_date,
        )
    )
    if request.device_id is not None:
        stmt = stmt.where(models.Session.device_id == request.device_id)
    row = (await db.execute(stmt)).one_or_none()

    if row is None:
        raise ValueError(
            f"Session {request.session_id} not found or not owned by "
            f"profile {profile_id} for date {request.therapy_date}"
        )

    session_start: datetime = row[0]
    return await _fetch_waveform_blobs(db, request, request.session_id, session_start)


def compute_waveform_window(raw: RawWaveformWindow) -> WaveformWindow:
    """Pure — no DB access. Deserializes bytes, slices window, applies LTTB."""

    from snore.analysis.data.waveform_loader import (  # noqa: PLC0415
        deserialize_waveform_blob,
    )
    from snore.services.lttb import lttb_downsample  # noqa: PLC0415

    request = raw.request
    channels_out: list[WaveformChannel] = []
    missing_channels: list[WaveformChannelName] = list(raw.missing_channels)

    for raw_ch in raw.channels:
        if raw_ch.sample_count <= 0 or not raw_ch.raw_bytes:
            missing_channels.append(raw_ch.waveform_type)
            continue
        try:
            timestamps, values = deserialize_waveform_blob(
                raw_ch.raw_bytes, raw_ch.sample_count
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid waveform data for channel '{raw_ch.waveform_type.value}'"
            ) from exc
        # Slice to requested window
        mask = (timestamps >= request.offset_start) & (timestamps <= request.offset_end)
        ts_slice = timestamps[mask]
        v_slice = values[mask]

        original_count = int(len(ts_slice))
        is_downsampled = False
        if request.max_points is not None and original_count > request.max_points:
            # LTTB downsampling: lttb_downsample(timestamps, values, target_points)
            if len(ts_slice) >= 3:
                ts_ds, v_ds = lttb_downsample(ts_slice, v_slice, request.max_points)
                ts_slice = ts_ds
                v_slice = v_ds
                is_downsampled = True

        channels_out.append(
            WaveformChannel(
                channel_type=raw_ch.waveform_type,
                unit=raw_ch.unit,
                sample_rate=raw_ch.sample_rate,
                offset_seconds=ts_slice.tolist(),
                values=v_slice.tolist(),
                original_sample_count=original_count,
                is_downsampled=is_downsampled,
            )
        )

    missing_reason: NullReason | None = (
        NullReason.CHANNEL_ABSENT if missing_channels else None
    )

    return WaveformWindow(
        session_id=raw.session_id,
        session_start_wall_clock=raw.session_start_wall_clock,
        window_start_offset=request.offset_start,
        window_end_offset=request.offset_end,
        channels=channels_out,
        missing_channels=missing_channels,
        missing_channel_reason=missing_reason,
    )


# ---------------------------------------------------------------------------
# §10 — Nightly-aggregation DTOs
# ---------------------------------------------------------------------------


class NightlyAnalysisSummary(BaseModel):
    """Latest-analysis-run fields for one therapy night."""

    therapy_date: date
    device_id: int

    day_status: DayAnalysisStatus
    session_coverage: list[SessionCoverage]
    eligible_session_count: int
    analyzed_session_count: int
    missing_or_stale_session_ids: list[int] = Field(default_factory=list)
    algorithm_identity: AlgorithmIdentity | None

    rera_count: int | None
    rera_reason: NullReason | None
    primary_mode: str | None
    fl_median: float | None
    fl_95th: float | None
    fl_max: float | None
    fl_reason: NullReason | None
    # Percent of leak-valid classified breaths with flow_class >= 4. Denominator
    # is leak-valid breaths with a non-null flow_class (consistent with
    # fl_median's leak-valid convention).
    fl_class_ge4_pct: float | None
    fl_class_ge4_pct_reason: NullReason | None

    ti_median_s: float | None
    ti_median_reason: NullReason | None
    ie_ratio_median: float | None
    ie_ratio_reason: NullReason | None

    total_therapy_hours: float
    compliance_threshold_hours: float
    is_compliant: bool

    rera_index: float | None = None
    rera_index_reason: NullReason | None = None
    rdi: float | None = None
    rdi_reason: NullReason | None = None


class NightlyRangeSummary(BaseModel):
    """Compliance + analysis summary over a date range."""

    date_start: date
    date_end: date
    device_id: int
    compliance_threshold_hours: float
    n_calendar_nights: int
    n_nights: int
    days_compliant: int
    compliance_pct: float
    nights: list[NightlyAnalysisSummary]


# ---------------------------------------------------------------------------
# §11 — DeviceCapabilities
# ---------------------------------------------------------------------------


class DeviceCapabilities(BaseModel):
    """What data is actually present for a device over a (requested) date range."""

    device_id: int
    requested_date_start: date | None
    requested_date_end: date | None
    actual_date_start: date | None
    actual_date_end: date | None
    null_reason: NullReason | None
    channels_present: list[str]
    all_setting_keys_present: list[str]
    rx_keys_present: list[str]
    event_types_present: list[str]
    session_count: int
    nights_with_data: int
    supported_vendor_models: list[str]

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None


# ---------------------------------------------------------------------------
# §12 — CA-analysis DTOs
# ---------------------------------------------------------------------------


class CaDetail(BaseModel):
    """Per-CA event analysis."""

    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    offset_seconds: float
    duration_seconds: float | None
    preceding_mv_slope: float | None
    preceding_mv_reason: NullReason | None
    ps_delivered_cmh2o: float | None
    ps_reason: NullReason | None
    stability_index: float | None
    stability_reason: NullReason | None
    # MV provenance: "device" | "flow_derived" | None (no MV channel available)
    mv_source: str | None = None


class CaAnalysisResult(BaseModel):
    """Result of get_ca_analysis()."""

    query_date: date
    device_id: int
    day_status: DayAnalysisStatus
    session_coverage: list[SessionCoverage] = Field(default_factory=list)
    algorithm_identity: AlgorithmIdentity | None
    null_reason: NullReason | None
    ca_events: list[CaDetail]
    periodic_breathing_pct: float | None
    pb_reason: NullReason | None
    mv_rolling_variance: float | None
    mv_variance_reason: NullReason | None
    # Night-level MV provenance: "device" | "flow_derived" | "mixed" | None,
    # aggregated across sessions that contributed an MV channel.
    mv_source: str | None = None
    mv_fallback_version: str = MV_FALLBACK_ALGO_VERSION


# ---------------------------------------------------------------------------
# §12 — CA-analysis fetch/compute seam (plan §9 conformance)
# ---------------------------------------------------------------------------


class RawCaEvent(BaseModel):
    """One CA event row (ORM-free). Input to compute_ca_analysis."""

    start_time: datetime  # naive — matches models.Event.start_time
    duration_seconds: float | None


class RawCaSessionData(BaseModel):
    """Per-session raw data for CA analysis (ORM-free).

    pre_waveform carries MV, THERAPY_PRESSURE, and EPAP blobs as raw bytes;
    compute_ca_analysis calls compute_waveform_window on each entry, preserving
    the single pre-fetch optimisation from the original get_ca_analysis.
    """

    session_id: int
    session_start: datetime  # naive — tier-2 anchor
    duration_seconds: float
    coverage: SessionCoverage
    is_ok: bool  # analysis_status == OK and algo_versions is not None and ar_id is not None
    pre_waveform: RawWaveformWindow  # MV, THERAPY_PRESSURE, EPAP channels
    # FLOW blobs, fetched only when the device MV channel is absent; input to
    # the derive_mv_from_flow fallback in compute_ca_analysis.
    flow_waveform: RawWaveformWindow | None = None
    ca_events: list[RawCaEvent]
    pb_json: (
        dict[str, Any] | None
    )  # programmatic_result_json for OK sessions; None otherwise


class RawCaAnalysis(BaseModel):
    """Raw fetch result for CA analysis. Pass to compute_ca_analysis (ORM-free).

    session_data being empty signals an empty day; compute_ca_analysis maps it
    to a CaAnalysisResult using the pre-reduced day-level fields below.
    Day-level state (day_status, algorithm_identity, null_reason) is pre-reduced
    during fetch so compute_ca_analysis performs only numpy/statistics work.
    """

    therapy_date: date
    device_id: int
    session_data: list[RawCaSessionData]
    # Pre-reduced day-level state (computed from coverage during fetch; no DB access)
    day_status: DayAnalysisStatus
    algorithm_identity: AlgorithmIdentity | None
    null_reason: NullReason | None


# ---------------------------------------------------------------------------
# §13 — BreathService helpers
# ---------------------------------------------------------------------------


def _count_fl_run_reras(
    breath_rows: Sequence[Any],
    fl_class_threshold: int = 4,
    min_fl_run_length: int = 2,
) -> int:
    """Count RERA-proxy events: FL runs ending in a recovery breath."""
    count = 0
    i = 0
    while i < len(breath_rows):
        b = breath_rows[i]
        if b.flow_class is not None and b.flow_class >= fl_class_threshold:
            run_start = i
            while (
                i < len(breath_rows)
                and breath_rows[i].flow_class is not None
                and breath_rows[i].flow_class >= fl_class_threshold
            ):
                i += 1
            run_len = i - run_start
            if (
                run_len >= min_fl_run_length
                and i < len(breath_rows)
                and breath_rows[i].is_recovery_breath
            ):
                count += 1
        else:
            i += 1
    return count


# ---------------------------------------------------------------------------
# §13 — BreathService
# ---------------------------------------------------------------------------


class BreathService:
    """Query layer over the breaths table. All methods are async.

    Every public method enforces profile ownership: all Session/Device/Day
    queries join through ``Device.profile_id == self._profile_id`` so that
    foreign-profile data is never returned.
    """

    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self._db = db_session
        self._profile_id = profile_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _latest_analysis_for_session(
        self, session_id: int
    ) -> tuple[AnalysisStatus, AlgoVersions | None, int | None]:
        """Return (status, algo_versions, analysis_result_id) for latest run.

        Ownership is assumed: callers are responsible for verifying the
        session belongs to ``self._profile_id`` via ``_resolve_range``
        or an explicit profile-scoped query before calling this helper.

        Returns (NOT_RUN, None, None) when no run exists.
        Returns (STALE_VERSION, algo|None, id) when engine_versions_json is stale.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        row = (
            (
                await self._db.execute(
                    select(models.AnalysisResult)
                    .where(models.AnalysisResult.session_id == session_id)
                    .order_by(
                        models.AnalysisResult.created_at.desc(),
                        models.AnalysisResult.id.desc(),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return AnalysisStatus.NOT_RUN, None, None
        status, algo = self._classify_analysis_row(row)
        return status, algo, row.id

    @staticmethod
    def _classify_analysis_row(
        row: Any,
    ) -> tuple[AnalysisStatus, AlgoVersions | None]:
        """Classify an AnalysisResult ORM row: (status, algo|None).

        Precondition: row is not None (callers verify before calling).
        """
        stored = row.engine_versions_json
        if not stored or "identity" not in stored:
            return AnalysisStatus.STALE_VERSION, None
        try:
            algo = AlgoVersions.model_validate(stored)
        except Exception:
            return AnalysisStatus.STALE_VERSION, None
        current = BreathService._current_algorithm_identity()
        if algo.identity.model_dump() != current.model_dump():
            return AnalysisStatus.STALE_VERSION, algo
        return AnalysisStatus.OK, algo

    # ------------------------------------------------------------------
    # Single range-aware resolver (replaces _resolve_device, _resolve_session_for_date,
    # and _fetch_day_sessions — all callers must use _resolve_range)
    # ------------------------------------------------------------------

    async def _resolve_range(
        self,
        date_start: date,
        date_end: date,
        device_id: int | None,
    ) -> tuple[int, dict[date, list[Any]]]:
        """Return (resolved_device_id, sessions_by_date) for [date_start, date_end].

        device_id given and owned by this profile:
            Validate ownership independent of data presence.
            Return (device_id, sessions_by_date) — sessions_by_date may be empty.
        device_id given and NOT owned:
            Raise DeviceNotOwnedError(device_id, profile_id).
        device_id None, 0 owned devices with sessions in range:
            Raise ValueError("No sessions found in range").
        device_id None, 1 distinct owned device in range:
            Auto-select it; return (device_id, sessions_by_date).
        device_id None, ≥2 distinct owned devices in range:
            Raise DeviceAmbiguityError with all owned device_ids.

        For a single-date point query call: _resolve_range(d, d, device_id).
        """
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        if device_id is not None:
            # Validate ownership independent of data presence
            owned = (
                await self._db.execute(
                    select(models.Device.id).where(
                        models.Device.id == device_id,
                        models.Device.profile_id == self._profile_id,
                    )
                )
            ).scalar_one_or_none()
            if owned is None:
                raise DeviceNotOwnedError(
                    device_id=device_id, profile_id=self._profile_id
                )
            # Fetch sessions in range (ownership already verified above)
            stmt = (
                select(models.Session, models.Day)
                .join(models.Day, models.Session.day_id == models.Day.id)
                .where(
                    models.Session.device_id == device_id,
                    models.Day.date >= date_start,
                    models.Day.date <= date_end,
                )
                .order_by(models.Day.date, models.Session.start_time)
            )
            rows = (await self._db.execute(stmt)).all()
            sessions_by_date: dict[date, list[Any]] = {}
            for r in rows:
                d = r.Day.date
                if d not in sessions_by_date:
                    sessions_by_date[d] = []
                sessions_by_date[d].append(r.Session)
            return device_id, sessions_by_date

        # device_id is None — auto-select from owned sessions in range
        stmt = (
            select(models.Session, models.Day)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(
                models.Day.date >= date_start,
                models.Day.date <= date_end,
                models.Device.profile_id == self._profile_id,
            )
            .order_by(models.Day.date, models.Session.start_time)
        )
        rows = (await self._db.execute(stmt)).all()
        if not rows:
            raise NoSessionsInRangeError(date_start, date_end)
        # Distinct device_ids, order-preserving
        device_ids_seen: list[int] = list(
            dict.fromkeys(r.Session.device_id for r in rows)
        )
        if len(device_ids_seen) > 1:
            serial_rows = (
                await self._db.execute(
                    select(models.Device.id, models.Device.serial_number).where(
                        models.Device.id.in_(device_ids_seen)
                    )
                )
            ).all()
            device_serials = {int(r[0]): (r[1] or "") for r in serial_rows}
            raise DeviceAmbiguityError(
                therapy_date=date_start,
                profile_id=self._profile_id,
                owned_device_ids=device_ids_seen,
                device_serials=device_serials,
            )
        resolved_device_id = device_ids_seen[0]
        sessions_by_date = {}
        for r in rows:
            d = r.Day.date
            if d not in sessions_by_date:
                sessions_by_date[d] = []
            sessions_by_date[d].append(r.Session)
        return resolved_device_id, sessions_by_date

    @staticmethod
    def _reduce_day_status(
        coverages: list[SessionCoverage],
        identities: list[AlgorithmIdentity],
    ) -> DayAnalysisStatus:
        """Reduce per-session coverage to a day-level DayAnalysisStatus.

        Precedence (plan §1 line 864):
        1. Multiple distinct algorithm identities among OK sessions → MIXED_VERSION
        2. All OK → OK
        3. All NOT_RUN → NOT_RUN
        4. All STALE_VERSION → STALE
        5. Anything else (stale+not-run, ok+stale, ok+not-run, …) → PARTIAL
        """
        if not coverages:
            return DayAnalysisStatus.NOT_RUN

        # plan §1 line 864 rule 1: multiple distinct identities → MIXED_VERSION
        if len(identities) > 1:
            id_strs = {str(i.model_dump()) for i in identities}
            if len(id_strs) > 1:
                return DayAnalysisStatus.MIXED_VERSION

        statuses = {c.analysis_status for c in coverages}

        # plan §1 line 864 rule 2: all OK → OK
        if statuses == {AnalysisStatus.OK}:
            return DayAnalysisStatus.OK

        # plan §1 line 864 rule 3: all NOT_RUN → NOT_RUN
        if statuses == {AnalysisStatus.NOT_RUN}:
            return DayAnalysisStatus.NOT_RUN

        # plan §1 line 864 rule 4: all STALE_VERSION → STALE
        if statuses == {AnalysisStatus.STALE_VERSION}:
            return DayAnalysisStatus.STALE

        # plan §1 line 864 rule 5: any other mix → PARTIAL
        return DayAnalysisStatus.PARTIAL

    # ------------------------------------------------------------------
    # §13 — Public seam methods
    # ------------------------------------------------------------------

    async def get_breath_table(self, query: BreathQueryRange) -> BreathPage:
        """Raw or binned breath fetch.

        Latest analysis run per session selected by (created_at DESC, id DESC).
        analysis_status=NOT_RUN when no AnalysisResult exists;
        STALE_VERSION when engine_versions_json differs from current identity.
        """
        from sqlalchemy import func, select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        # Resolve session_id
        if query.session_id is not None:
            session_id = query.session_id
            # Verify ownership: session must belong to this profile and date
            session_stmt = (
                select(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    models.Session.id == session_id,
                    models.Device.profile_id == self._profile_id,
                    models.Session.day_id.in_(
                        select(models.Day.id).where(
                            models.Day.date == query.therapy_date
                        )
                    ),
                )
            )
            session_row = (await self._db.execute(session_stmt)).scalars().first()
            if session_row is None:
                raise ValueError(
                    f"session_id {session_id} does not belong to date {query.therapy_date}"
                    " or is not owned by this profile"
                )
            device_id = session_row.device_id
            # If caller also specified device_id, verify the session belongs to it
            if query.device_id is not None and device_id != query.device_id:
                raise ValueError(
                    f"session_id {session_id} belongs to device {device_id},"
                    f" not requested device {query.device_id}"
                )
        else:
            # Use _resolve_range for point query; require exactly one session on the date
            resolved_device_id, sessions_by_date = await self._resolve_range(
                query.therapy_date, query.therapy_date, query.device_id
            )
            day_sessions = sessions_by_date.get(query.therapy_date, [])
            if not day_sessions:
                raise ValueError(f"No sessions found for date {query.therapy_date}")
            if len(day_sessions) > 1:
                sessions_list = [
                    SessionSummary(
                        session_id=s.id,
                        start_wall_clock=s.start_time,
                        duration_seconds=s.duration_seconds or 0.0,
                    )
                    for s in day_sessions
                ]
                raise MultiSessionAmbiguityError(
                    therapy_date=query.therapy_date,
                    device_id=resolved_device_id,
                    sessions=sessions_list,
                )
            session_id = day_sessions[0].id
            device_id = resolved_device_id

        (
            analysis_status,
            algo_versions,
            analysis_result_id,
        ) = await self._latest_analysis_for_session(session_id)

        # Get session start for wall-clock anchoring
        sess_row = (
            (
                await self._db.execute(
                    select(models.Session).where(models.Session.id == session_id)
                )
            )
            .scalars()
            .first()
        )
        session_start: datetime = sess_row.start_time if sess_row else datetime.min

        # No analysis run
        if analysis_result_id is None or analysis_status == AnalysisStatus.NOT_RUN:
            return BreathPage(
                query=query,
                analysis_status=AnalysisStatus.NOT_RUN,
                algo_versions=None,
                null_reason=NullReason.ANALYSIS_NOT_RUN,
                is_binned=query.bin_minutes is not None,
                total_breaths=0,
                page=query.page,
                page_size=query.page_size,
                session_id=session_id,
            )

        # Stale version — return empty page with status
        if analysis_status == AnalysisStatus.STALE_VERSION:
            return BreathPage(
                query=query,
                analysis_status=AnalysisStatus.STALE_VERSION,
                algo_versions=algo_versions,
                null_reason=NullReason.ANALYSIS_STALE,
                is_binned=query.bin_minutes is not None,
                total_breaths=0,
                page=query.page,
                page_size=query.page_size,
                session_id=session_id,
            )

        # Fetch matching breaths
        base_stmt = (
            select(models.Breath)
            .where(
                models.Breath.analysis_result_id == analysis_result_id,
                models.Breath.start_offset_s >= query.offset_start,
                models.Breath.end_offset_s <= query.offset_end,
            )
            .order_by(models.Breath.session_id, models.Breath.breath_number)
        )

        total_result = await self._db.execute(
            select(func.count()).select_from(base_stmt.subquery())
        )
        total_breaths = total_result.scalar_one()

        if query.bin_minutes is None:
            # Raw fetch with pagination
            offset_rows = (query.page - 1) * query.page_size
            paginated = base_stmt.offset(offset_rows).limit(query.page_size)
            breath_rows = (await self._db.execute(paginated)).scalars().all()

            rows = [
                BreathRow(
                    analysis_result_id=b.analysis_result_id,
                    session_id=b.session_id,
                    breath_number=b.breath_number,
                    session_start_wall_clock=session_start,
                    start_offset_seconds=b.start_offset_s,
                    end_offset_seconds=b.end_offset_s,
                    ti=b.inspiration_time_s,
                    te=b.expiration_time_s,
                    ttot=b.total_time_s,
                    ie_ratio=b.i_e_ratio,
                    duty_cycle=b.duty_cycle,
                    peak_insp_flow=b.peak_flow_lpm,
                    peak_exp_flow=b.peak_exp_flow_lpm,
                    tidal_volume=b.tidal_volume_ml,
                    flatness_index=b.flatness_index,
                    mid_insp_flattening=b.mid_insp_flattening,
                    flow_class=b.flow_class,
                    flow_class_confidence=b.flow_confidence,
                    is_recovery_breath=b.is_recovery_breath,
                    trigger_type=(
                        TriggerType(b.inferred_trigger_type)
                        if b.inferred_trigger_type
                        else None
                    ),
                    cycle_type=(
                        CycleType(b.inferred_cycle_type)
                        if b.inferred_cycle_type
                        else None
                    ),
                    trigger_cycle_confidence=b.trigger_confidence,
                    trigger_cycle_applicability=(
                        TriggerCycleApplicability.VALIDATED
                        if b.trigger_cycle_applicable is True
                        else (
                            TriggerCycleApplicability.UNVALIDATED_DEVICE
                            if b.trigger_cycle_applicable is False
                            else None
                        )
                    ),
                    trigger_cycle_reason=(
                        NullReason(b.trigger_cycle_reason)
                        if b.trigger_cycle_reason
                        else None
                    ),
                    leak_valid=b.leak_valid,
                    leak_valid_reason=(
                        NullReason(b.leak_valid_reason) if b.leak_valid_reason else None
                    ),
                    ramp_active=b.ramp_active,
                    ramp_active_reason=(
                        NullReason(b.ramp_active_reason)
                        if b.ramp_active_reason
                        else None
                    ),
                    mask_off=b.mask_off,
                    mask_off_reason=(
                        NullReason(b.mask_off_reason) if b.mask_off_reason else None
                    ),
                )
                for b in breath_rows
            ]

            return BreathPage(
                query=query,
                analysis_status=analysis_status,
                algo_versions=algo_versions,
                null_reason=None,
                is_binned=False,
                total_breaths=total_breaths,
                page=query.page,
                page_size=query.page_size,
                rows=rows,
                session_id=session_id,
            )
        else:
            # Binned fetch — load all matching breaths then aggregate
            import statistics  # noqa: PLC0415

            all_breaths = (await self._db.execute(base_stmt)).scalars().all()
            bin_secs = query.bin_minutes * 60.0
            bins: list[BreathBin] = []

            # Group breaths into time bins
            bin_start = query.offset_start
            while bin_start < query.offset_end:
                bin_end = min(bin_start + bin_secs, query.offset_end)
                bin_breaths = [
                    b
                    for b in all_breaths
                    if b.start_offset_s >= bin_start and b.start_offset_s < bin_end
                ]
                if bin_breaths:
                    fi_vals = [
                        b.flatness_index
                        for b in bin_breaths
                        if b.flatness_index is not None
                    ]
                    mif_vals = [
                        b.mid_insp_flattening
                        for b in bin_breaths
                        if b.mid_insp_flattening is not None
                    ]
                    tv_vals = [
                        b.tidal_volume_ml
                        for b in bin_breaths
                        if b.tidal_volume_ml is not None
                    ]
                    ie_vals = [
                        b.i_e_ratio for b in bin_breaths if b.i_e_ratio is not None
                    ]
                    fc_vals = [
                        b.flow_class for b in bin_breaths if b.flow_class is not None
                    ]
                    lv_count = sum(1 for b in bin_breaths if b.leak_valid is True)
                    lv_eligible = sum(
                        1 for b in bin_breaths if b.leak_valid is not None
                    )

                    bins.append(
                        BreathBin(
                            session_start_wall_clock=session_start,
                            bin_start_offset=bin_start,
                            bin_end_offset=bin_end,
                            breath_count=len(bin_breaths),
                            flatness_index_median=(
                                statistics.median(fi_vals) if fi_vals else None
                            ),
                            mid_insp_flattening_median=(
                                statistics.median(mif_vals) if mif_vals else None
                            ),
                            flow_class_mode=(
                                max(set(fc_vals), key=fc_vals.count)
                                if fc_vals
                                else None
                            ),
                            tidal_volume_median=(
                                statistics.median(tv_vals) if tv_vals else None
                            ),
                            ie_ratio_median=(
                                statistics.median(ie_vals) if ie_vals else None
                            ),
                            leak_valid_fraction=(
                                lv_count / lv_eligible if lv_eligible > 0 else None
                            ),
                            analysis_status=analysis_status,
                        )
                    )
                bin_start = bin_end

            return BreathPage(
                query=query,
                analysis_status=analysis_status,
                algo_versions=algo_versions,
                null_reason=None,
                is_binned=True,
                total_breaths=total_breaths,
                page=1,
                page_size=query.page_size,
                bins=bins,
                session_id=session_id,
            )

    async def find_windows(
        self,
        therapy_date: date,
        criterion: WindowCriterion,
        n: int,
        options: WindowCriterionOptions | None = None,
        device_id: int | None = None,
    ) -> FindWindowsResult:
        """N windows matching criterion, worst first.

        See Appendix A §6 for full construction rules and dedup logic.
        """
        opts = options or WindowCriterionOptions()

        # Validate criterion-irrelevant options per §6 docstring
        defaults = WindowCriterionOptions()
        if criterion == WindowCriterion.WORST_FLATTENING_LEAK_VALID:
            bad = [
                f
                for f in ("context_seconds", "min_fl_run_length", "fl_class_threshold")
                if getattr(opts, f) != getattr(defaults, f)
            ]
            if bad:
                raise ValueError(
                    f"Options irrelevant to WORST_FLATTENING_LEAK_VALID: {bad}"
                )
        elif criterion == WindowCriterion.CA_CENTERED:
            bad = [
                f
                for f in (
                    "include_unknown_leak",
                    "flattening_threshold",
                    "min_window_breaths",
                    "context_breaths_before",
                    "context_breaths_after",
                    "min_fl_run_length",
                    "fl_class_threshold",
                )
                if getattr(opts, f) != getattr(defaults, f)
            ]
            if bad:
                raise ValueError(f"Options irrelevant to CA_CENTERED: {bad}")
        elif criterion == WindowCriterion.FL_RUN_ENDING_IN_RECOVERY:
            bad = [
                f
                for f in (
                    "include_unknown_leak",
                    "flattening_threshold",
                    "min_window_breaths",
                    "context_breaths_before",
                    "context_breaths_after",
                    "context_seconds",
                )
                if getattr(opts, f) != getattr(defaults, f)
            ]
            if bad:
                raise ValueError(
                    f"Options irrelevant to FL_RUN_ENDING_IN_RECOVERY: {bad}"
                )

        # Resolve device (raises DeviceAmbiguityError for ≥2 devices, ValueError for 0)
        try:
            resolved_device_id, sessions_by_date = await self._resolve_range(
                therapy_date, therapy_date, device_id
            )
        except ValueError:
            return FindWindowsResult(
                query_date=therapy_date,
                device_id=device_id or 0,
                criterion=criterion,
                day_status=DayAnalysisStatus.NOT_RUN,
                session_coverage=[],
                algorithm_identity=None,
                null_reason=NullReason.ANALYSIS_NOT_RUN,
                primary_mode=None,
                windows=[],
            )
        # DeviceAmbiguityError propagates to caller

        day_sessions = sessions_by_date.get(therapy_date, [])
        if not day_sessions:
            return FindWindowsResult(
                query_date=therapy_date,
                device_id=resolved_device_id,
                criterion=criterion,
                day_status=DayAnalysisStatus.NOT_RUN,
                session_coverage=[],
                algorithm_identity=None,
                null_reason=NullReason.ANALYSIS_NOT_RUN,
                primary_mode=None,
                windows=[],
            )

        # Build per-session analysis status
        session_ids = [s.id for s in day_sessions]
        session_starts = {s.id: s.start_time for s in day_sessions}

        coverage: list[SessionCoverage] = []
        identities: list[AlgorithmIdentity] = []
        primary_modes: list[str] = []
        ar_by_session: dict[int, int | None] = {}
        for sid in session_ids:
            status, algo, ar_id = await self._latest_analysis_for_session(sid)
            ar_by_session[sid] = ar_id
            coverage.append(
                SessionCoverage(
                    session_id=sid, analysis_status=status, algo_versions=algo
                )
            )
            if status == AnalysisStatus.OK and algo is not None:
                identities.append(algo.identity)
                primary_modes.append(algo.run.primary_mode)

        # Determine day_status via centralized reducer (plan §1 line 864)
        day_status = self._reduce_day_status(coverage, identities)

        # Check identity uniformity for CROSS_VERSION_REFUSAL_KEYS
        uniform_identity: AlgorithmIdentity | None = None
        if identities:
            first_id = identities[0].model_dump()
            cross_keys = CROSS_VERSION_REFUSAL_KEYS
            all_same = all(
                {k: id_.model_dump()[k] for k in cross_keys}
                == {k: first_id[k] for k in cross_keys}
                for id_ in identities[1:]
            )
            if all_same:
                uniform_identity = identities[0]
            else:
                # MIXED_VERSION for FL-ranked criteria
                if criterion != WindowCriterion.CA_CENTERED:
                    return FindWindowsResult(
                        query_date=therapy_date,
                        device_id=resolved_device_id,
                        criterion=criterion,
                        day_status=DayAnalysisStatus.MIXED_VERSION,
                        session_coverage=coverage,
                        algorithm_identity=None,
                        null_reason=NullReason.ALGO_VERSION_MISMATCH,
                        primary_mode=None,
                        windows=[],
                    )

        # FL_RUN_ENDING_IN_RECOVERY: also requires uniform primary_mode
        uniform_primary_mode: str | None = None
        if primary_modes:
            if len(set(primary_modes)) == 1:
                uniform_primary_mode = primary_modes[0]
            elif criterion == WindowCriterion.FL_RUN_ENDING_IN_RECOVERY:
                return FindWindowsResult(
                    query_date=therapy_date,
                    device_id=resolved_device_id,
                    criterion=criterion,
                    day_status=day_status,
                    session_coverage=coverage,
                    algorithm_identity=uniform_identity,
                    null_reason=NullReason.PRIMARY_MODE_MISMATCH,
                    primary_mode=None,
                    windows=[],
                )

        result_primary_mode = uniform_primary_mode

        # ar_by_session populated during the coverage loop above
        ar_status_by_session: dict[int, AnalysisStatus] = {
            c.session_id: c.analysis_status for c in coverage
        }

        # Build windows per criterion
        windows: list[WindowResult] = []

        if criterion == WindowCriterion.WORST_FLATTENING_LEAK_VALID:
            windows = await self._find_worst_flattening_windows(
                session_ids=session_ids,
                session_starts=session_starts,
                ar_by_session=ar_by_session,
                ar_status_by_session=ar_status_by_session,
                n=n,
                opts=opts,
            )

        elif criterion == WindowCriterion.CA_CENTERED:
            windows = await self._find_ca_centered_windows(
                therapy_date=therapy_date,
                device_id=resolved_device_id,
                session_starts=session_starts,
                ar_by_session=ar_by_session,
                ar_status_by_session=ar_status_by_session,
                n=n,
                opts=opts,
            )

        elif criterion == WindowCriterion.FL_RUN_ENDING_IN_RECOVERY:
            windows = await self._find_fl_run_windows(
                session_ids=session_ids,
                session_starts=session_starts,
                ar_by_session=ar_by_session,
                ar_status_by_session=ar_status_by_session,
                n=n,
                opts=opts,
            )

        return FindWindowsResult(
            query_date=therapy_date,
            device_id=resolved_device_id,
            criterion=criterion,
            day_status=day_status,
            session_coverage=coverage,
            algorithm_identity=uniform_identity,
            null_reason=None,
            primary_mode=result_primary_mode,
            windows=windows,
        )

    async def _find_worst_flattening_windows(
        self,
        session_ids: list[int],
        session_starts: dict[int, datetime],
        ar_by_session: dict[int, int | None],
        ar_status_by_session: dict[int, AnalysisStatus],
        n: int,
        opts: WindowCriterionOptions,
    ) -> list[WindowResult]:
        """Build WORST_FLATTENING_LEAK_VALID windows per §6 construction rule."""
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        candidates: list[WindowResult] = []
        for sid in session_ids:
            ar_id = ar_by_session.get(sid)
            ar_status = ar_status_by_session.get(sid, AnalysisStatus.NOT_RUN)
            if ar_id is None or ar_status != AnalysisStatus.OK:
                continue

            # Fetch all breaths for this session ordered by breath_number
            breath_rows = (
                (
                    await self._db.execute(
                        select(models.Breath)
                        .where(models.Breath.analysis_result_id == ar_id)
                        .order_by(models.Breath.breath_number)
                    )
                )
                .scalars()
                .all()
            )
            if not breath_rows:
                continue

            # Filter eligible anchors per §6 step 1
            eligible_indices: list[int] = []
            for i, b in enumerate(breath_rows):
                if b.mid_insp_flattening is None:
                    continue
                if b.leak_valid is True or (
                    opts.include_unknown_leak and b.leak_valid is None
                ):
                    if (
                        opts.flattening_threshold is None
                        or b.mid_insp_flattening >= opts.flattening_threshold
                    ):
                        eligible_indices.append(i)

            # Sort by mid_insp_flattening descending (§6 step 2)
            eligible_indices.sort(
                key=lambda i: cast(float, breath_rows[i].mid_insp_flattening),
                reverse=True,
            )

            session_start = session_starts[sid]
            for anchor_idx in eligible_indices:
                # §6 step 3: form candidate window
                start_idx = max(0, anchor_idx - opts.context_breaths_before)
                end_idx = min(
                    len(breath_rows) - 1, anchor_idx + opts.context_breaths_after
                )
                window_breaths = breath_rows[start_idx : end_idx + 1]

                if len(window_breaths) < opts.min_window_breaths:
                    continue

                anchor_b = breath_rows[anchor_idx]
                candidates.append(
                    WindowResult(
                        criterion=WindowCriterion.WORST_FLATTENING_LEAK_VALID,
                        session_id=sid,
                        session_start_wall_clock=session_start,
                        window_start_offset=window_breaths[0].start_offset_s,
                        window_end_offset=window_breaths[-1].end_offset_s,
                        reason_summary=(
                            f"fl_index={anchor_b.mid_insp_flattening:.3f}, "
                            f"{len(window_breaths)} breaths"
                        ),
                        worst_mid_insp_flattening=anchor_b.mid_insp_flattening,
                        fl_run_length=None,
                        anchor_event_offset=None,
                        analysis_result_id=ar_id,
                        analysis_status=ar_status,
                        analysis_reason=None,
                    )
                )

        return self._dedup_and_top_n(
            candidates, n, key=lambda w: w.worst_mid_insp_flattening or 0.0
        )

    async def _find_ca_centered_windows(
        self,
        therapy_date: date,
        device_id: int,
        session_starts: dict[int, datetime],
        ar_by_session: dict[int, int | None],
        ar_status_by_session: dict[int, AnalysisStatus],
        n: int,
        opts: WindowCriterionOptions,
    ) -> list[WindowResult]:
        """Build CA_CENTERED windows — anchored on Event rows (CA_CENTERED proceeds
        on any day_status including NOT_RUN, per §6 pass-3 IMPORTANT-5)."""
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        stmt = (
            select(models.Event, models.Session)
            .join(models.Session, models.Event.session_id == models.Session.id)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(
                models.Day.date == therapy_date,
                models.Session.device_id == device_id,
                models.Event.event_type == "CA",
            )
            .order_by(models.Event.start_time)
        )
        event_rows = (await self._db.execute(stmt)).all()

        candidates: list[WindowResult] = []
        for ev_row in event_rows:
            ev = ev_row.Event
            sess = ev_row.Session
            sid = sess.id
            session_start = session_starts.get(sid, sess.start_time)
            # offset from session start
            ev_offset = (ev.start_time - session_start).total_seconds()
            win_start = max(0.0, ev_offset - opts.context_seconds)
            win_end = ev_offset + opts.context_seconds

            ar_id = ar_by_session.get(sid)
            ar_status = ar_status_by_session.get(sid, AnalysisStatus.NOT_RUN)

            candidates.append(
                WindowResult(
                    criterion=WindowCriterion.CA_CENTERED,
                    session_id=sid,
                    session_start_wall_clock=session_start,
                    window_start_offset=win_start,
                    window_end_offset=win_end,
                    reason_summary=f"CA event at offset {ev_offset:.1f}s",
                    worst_mid_insp_flattening=None,
                    fl_run_length=None,
                    anchor_event_offset=ev_offset,
                    analysis_result_id=ar_id,
                    analysis_status=ar_status,
                    analysis_reason=(
                        NullReason.ANALYSIS_NOT_RUN if ar_id is None else None
                    ),
                )
            )

        return self._dedup_and_top_n(
            candidates, n, key=lambda w: -(w.anchor_event_offset or 0.0)
        )

    async def _find_fl_run_windows(
        self,
        session_ids: list[int],
        session_starts: dict[int, datetime],
        ar_by_session: dict[int, int | None],
        ar_status_by_session: dict[int, AnalysisStatus],
        n: int,
        opts: WindowCriterionOptions,
    ) -> list[WindowResult]:
        """Build FL_RUN_ENDING_IN_RECOVERY windows — RERA-proxy: runs of ≥N consecutive
        FL breaths ending with is_recovery_breath=True."""
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        candidates: list[WindowResult] = []
        for sid in session_ids:
            ar_id = ar_by_session.get(sid)
            ar_status = ar_status_by_session.get(sid, AnalysisStatus.NOT_RUN)
            if ar_id is None or ar_status != AnalysisStatus.OK:
                continue

            breath_rows = (
                (
                    await self._db.execute(
                        select(models.Breath)
                        .where(models.Breath.analysis_result_id == ar_id)
                        .order_by(models.Breath.breath_number)
                    )
                )
                .scalars()
                .all()
            )
            if not breath_rows:
                continue

            session_start = session_starts[sid]
            # Scan for FL runs ending in recovery breath
            i = 0
            while i < len(breath_rows):
                b = breath_rows[i]
                if b.flow_class is not None and b.flow_class >= opts.fl_class_threshold:
                    # Start of a potential FL run
                    run_start = i
                    j = i
                    while j < len(breath_rows) and (
                        breath_rows[j].flow_class is not None
                        and (breath_rows[j].flow_class or 0) >= opts.fl_class_threshold
                    ):
                        j += 1
                    fl_run = breath_rows[run_start:j]
                    # Check if followed by a recovery breath
                    if j < len(breath_rows) and breath_rows[j].is_recovery_breath:
                        run_end_idx = j  # recovery breath
                        full_run = breath_rows[run_start : run_end_idx + 1]
                        fl_length = len(fl_run)
                        if fl_length >= opts.min_fl_run_length:
                            candidates.append(
                                WindowResult(
                                    criterion=WindowCriterion.FL_RUN_ENDING_IN_RECOVERY,
                                    session_id=sid,
                                    session_start_wall_clock=session_start,
                                    window_start_offset=full_run[0].start_offset_s,
                                    window_end_offset=full_run[-1].end_offset_s,
                                    reason_summary=(
                                        f"fl_run={fl_length} breaths, ends in recovery"
                                    ),
                                    worst_mid_insp_flattening=max(
                                        (
                                            b.mid_insp_flattening
                                            for b in fl_run
                                            if b.mid_insp_flattening is not None
                                        ),
                                        default=None,
                                    ),
                                    fl_run_length=fl_length,
                                    anchor_event_offset=None,
                                    analysis_result_id=ar_id,
                                    analysis_status=ar_status,
                                    analysis_reason=None,
                                )
                            )
                        i = run_end_idx + 1
                        continue
                    i = j
                else:
                    i += 1

        return self._dedup_and_top_n(candidates, n, key=lambda w: w.fl_run_length or 0)

    @staticmethod
    def _dedup_and_top_n(
        candidates: list[WindowResult],
        n: int,
        key: Callable[[WindowResult], Any],
    ) -> list[WindowResult]:
        """Deduplicate overlapping windows (>50% of shorter), keep worst; return top-N."""
        # Sort by severity descending (largest key first)
        sorted_cands = sorted(candidates, key=key, reverse=True)
        kept: list[WindowResult] = []
        for cand in sorted_cands:
            overlaps = False
            for existing in kept:
                if existing.session_id != cand.session_id:
                    continue
                overlap_start = max(
                    existing.window_start_offset, cand.window_start_offset
                )
                overlap_end = min(existing.window_end_offset, cand.window_end_offset)
                if overlap_end <= overlap_start:
                    continue
                overlap_len = overlap_end - overlap_start
                shorter = min(
                    existing.window_end_offset - existing.window_start_offset,
                    cand.window_end_offset - cand.window_start_offset,
                )
                if shorter > 0 and overlap_len / shorter > 0.5:
                    overlaps = True
                    break
            if not overlaps:
                kept.append(cand)
            if len(kept) >= n:
                break
        return kept

    async def compare_epochs(
        self,
        epochs: list[EpochRequest],
        metrics: list[DistributionMetric] | None = None,
    ) -> CompareEpochsResult:
        """Distributions across RxTracker epochs.

        Two-phase design: metadata checks (RX + identity) run across ALL epochs
        BEFORE any breath queries.  Any failure nulls all epoch distributions.

        Refuses on CROSS_VERSION_REFUSAL_KEYS mismatch (ALGO_VERSION_MISMATCH)
        or mid-epoch RX change (RX_CHANGED_WITHIN_EPOCH).  Mixed primary modes
        degrade RERA fields only (PRIMARY_MODE_MISMATCH).
        """
        import statistics  # noqa: PLC0415

        from sqlalchemy import select  # noqa: PLC0415

        from snore.analysis.rx_tracker import (  # noqa: PLC0415
            RX_KEYS,
            changed_setting_keys,
        )
        from snore.database import models  # noqa: PLC0415

        # Validate date order for each epoch before anything else
        for epoch in epochs:
            if epoch.date_start > epoch.date_end:
                raise ValueError(
                    f"Epoch '{epoch.label}': date_start ({epoch.date_start})"
                    f" must be <= date_end ({epoch.date_end})"
                )

        _null_dist = DistributionStats(
            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
        )

        # -----------------------------------------------------------------------
        # Phase 1: Resolve ONE device for the whole comparison, then per-epoch sessions.
        # All epochs must target the same device.  Union-resolve fires DeviceAmbiguityError
        # if multiple owned devices span the combined date range and no device_id is given.
        # -----------------------------------------------------------------------

        explicit_device_ids = {e.device_id for e in epochs if e.device_id is not None}
        if len(explicit_device_ids) > 1:
            raise ValueError(
                "All epochs in a comparison must target the same device_id"
            )
        union_device_id = explicit_device_ids.pop() if explicit_device_ids else None
        union_start = min(e.date_start for e in epochs)
        union_end = max(e.date_end for e in epochs)
        # Union-resolve to guarantee one device across all epochs.
        # DeviceAmbiguityError (multi-device profile, no device_id) propagates.
        # ValueError for a foreign/unknown explicit device_id: return NOT_AVAILABLE for all epochs.
        try:
            union_resolved_device_id, _ = await self._resolve_range(
                union_start, union_end, union_device_id
            )
        except DeviceAmbiguityError:
            raise
        except DeviceNotOwnedError:
            # Explicit foreign device → NOT_AVAILABLE for all epochs
            not_avail_epochs = [
                EpochBreathStats(
                    label=e.label,
                    date_start=e.date_start,
                    date_end=e.date_end,
                    nights_with_data=0,
                    nights_missing_analysis=0,
                    algorithm_identity=None,
                    null_reason=NullReason.NOT_AVAILABLE,
                    primary_mode=None,
                    mid_insp_flattening=_null_dist,
                    flatness_index=_null_dist,
                    flow_class_distribution={},
                    tidal_volume_ml=_null_dist,
                    ie_ratio=_null_dist,
                    rera_proxy_count=None,
                    rera_reason=NullReason.NOT_AVAILABLE,
                    rx_settings={},
                )
                for e in epochs
            ]
            return CompareEpochsResult(
                null_reason=NullReason.NOT_AVAILABLE,
                epochs=not_avail_epochs,
            )
        except ValueError:
            # ValueError here means auto-select found no sessions in range — always
            # NO_DATA_IN_RANGE (the NOT_AVAILABLE arm required union_device_id is not None,
            # but _resolve_range raises DeviceNotOwnedError for foreign explicit devices).
            no_data_reason = NullReason.NO_DATA_IN_RANGE
            null_epochs = [
                EpochBreathStats(
                    label=e.label,
                    date_start=e.date_start,
                    date_end=e.date_end,
                    nights_with_data=0,
                    nights_missing_analysis=0,
                    algorithm_identity=None,
                    null_reason=no_data_reason,
                    primary_mode=None,
                    mid_insp_flattening=_null_dist,
                    flatness_index=_null_dist,
                    flow_class_distribution={},
                    tidal_volume_ml=_null_dist,
                    ie_ratio=_null_dist,
                    rera_proxy_count=None,
                    rera_reason=NullReason.NOT_AVAILABLE,
                    rx_settings={},
                )
                for e in epochs
            ]
            return CompareEpochsResult(
                null_reason=no_data_reason,
                epochs=null_epochs,
            )

        # Each entry: dict with epoch metadata + resolved sessions + RX data
        epoch_resolved: list[dict[str, Any]] = []
        rx_violations: list[EpochRxViolation] = []

        for epoch in epochs:
            try:
                resolved_device_id, sessions_by_date = await self._resolve_range(
                    epoch.date_start, epoch.date_end, union_resolved_device_id
                )
            except ValueError:
                # Foreign/unknown device_id → NOT_AVAILABLE; no sessions → NO_DATA_IN_RANGE
                no_data_reason = (
                    NullReason.NO_DATA_IN_RANGE
                    if epoch.device_id is None
                    else NullReason.NOT_AVAILABLE
                )
                epoch_resolved.append(
                    {
                        "epoch": epoch,
                        "null_reason": no_data_reason,
                        "contributing_sessions": [],
                        "all_rx": [],
                        "nights_with_data": 0,
                        "nights_missing_analysis": 0,
                        "rx_violation": None,
                        "ar_ids": {},
                    }
                )
                continue
            # DeviceAmbiguityError propagates to caller

            # Collect per-session RX snapshots and contributing sessions (OK only)
            contributing_sessions: list[tuple[int, AlgoVersions]] = []
            # Each entry: (therapy_date, rx_dict) for change-date tracking
            session_rx_dated: list[tuple[date, dict[str, str]]] = []
            nights_with_data = 0
            nights_missing_analysis = 0
            ar_ids_for_epoch: dict[int, int | None] = {}

            for therapy_date, sessions in sessions_by_date.items():
                ok_on_date: list[tuple[int, AlgoVersions]] = []
                for sess in sessions:
                    status, algo, ar_id = await self._latest_analysis_for_session(
                        sess.id
                    )
                    if status == AnalysisStatus.OK and algo is not None:
                        ok_on_date.append((sess.id, algo))
                        ar_ids_for_epoch[sess.id] = ar_id
                        # Per-session RX snapshot (not merged per-day)
                        setting_rows = (
                            (
                                await self._db.execute(
                                    select(models.Setting).where(
                                        models.Setting.session_id == sess.id,
                                        models.Setting.key.in_(RX_KEYS),
                                        models.Setting.value.is_not(None),
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        rx_snap = {s.key: s.value for s in setting_rows if s.value}
                        session_rx_dated.append((therapy_date, rx_snap))

                if ok_on_date:
                    nights_with_data += 1
                    contributing_sessions.extend(ok_on_date)
                else:
                    nights_missing_analysis += 1

            # Within-epoch RX homogeneity check (per-session, not per-day merged)
            rx_violation: EpochRxViolation | None = None
            if len(session_rx_dated) > 1:
                first_rx_snap = session_rx_dated[0][1]
                if any(rx != first_rx_snap for _, rx in session_rx_dated[1:]):
                    change_dates: list[date] = []
                    changed_keys: set[str] = set()
                    prev_rx = session_rx_dated[0][1]
                    for snap_date, snap_rx in session_rx_dated[1:]:
                        diffs = changed_setting_keys(prev_rx, snap_rx)
                        if diffs:
                            changed_keys |= diffs
                            change_dates.append(snap_date)
                        prev_rx = snap_rx
                    rx_violation = EpochRxViolation(
                        epoch_label=epoch.label,
                        changed_keys=sorted(changed_keys),
                        change_dates=change_dates,
                    )
                    rx_violations.append(rx_violation)

            epoch_resolved.append(
                {
                    "epoch": epoch,
                    "null_reason": None
                    if contributing_sessions
                    else NullReason.NO_DATA_IN_RANGE,
                    "contributing_sessions": contributing_sessions,
                    "all_rx": [rx for _, rx in session_rx_dated],
                    "nights_with_data": nights_with_data,
                    "nights_missing_analysis": nights_missing_analysis,
                    "rx_violation": rx_violation,
                    "ar_ids": ar_ids_for_epoch,
                }
            )

        # -----------------------------------------------------------------------
        # Phase 2: Cross-epoch identity check (BEFORE any breath queries)
        # -----------------------------------------------------------------------

        # Gather all identities from ALL contributing sessions across ALL epochs
        all_identities_combined: list[AlgorithmIdentity] = [
            algo.identity
            for ed in epoch_resolved
            for _, algo in ed["contributing_sessions"]
        ]

        cross_epoch_mismatch = False
        if len(all_identities_combined) > 1:
            cross_keys = CROSS_VERSION_REFUSAL_KEYS
            first_cross_id = {
                k: all_identities_combined[0].model_dump()[k] for k in cross_keys
            }
            cross_epoch_mismatch = any(
                {k: id_.model_dump()[k] for k in cross_keys} != first_cross_id
                for id_ in all_identities_combined[1:]
            )

        has_rx_violation = bool(rx_violations)

        # If any check failed, return null payloads for ALL epochs immediately
        if has_rx_violation or cross_epoch_mismatch:
            refusal_reason = (
                NullReason.RX_CHANGED_WITHIN_EPOCH
                if has_rx_violation
                else NullReason.ALGO_VERSION_MISMATCH
            )
            epoch_stats: list[EpochBreathStats] = [
                EpochBreathStats(
                    label=ed["epoch"].label,
                    date_start=ed["epoch"].date_start,
                    date_end=ed["epoch"].date_end,
                    nights_with_data=ed["nights_with_data"],
                    nights_missing_analysis=ed["nights_missing_analysis"],
                    algorithm_identity=None,
                    null_reason=refusal_reason,
                    primary_mode=None,
                    mid_insp_flattening=_null_dist,
                    flatness_index=_null_dist,
                    flow_class_distribution={},
                    tidal_volume_ml=_null_dist,
                    ie_ratio=_null_dist,
                    rera_proxy_count=None,
                    rera_reason=refusal_reason,
                    rx_settings=ed["all_rx"][0] if ed["all_rx"] else {},
                )
                for ed in epoch_resolved
            ]
            return CompareEpochsResult(
                epochs=epoch_stats,
                null_reason=refusal_reason,
                rx_violations=rx_violations,
            )

        # -----------------------------------------------------------------------
        # Phase 3: Compute distributions (only if all checks passed)
        # -----------------------------------------------------------------------

        def _distrib(
            vals: list[float], n_breaths: int, n_nights: int
        ) -> DistributionStats:
            if not vals:
                return DistributionStats(
                    median=None,
                    iqr=None,
                    p95=None,
                    n_breaths=n_breaths,
                    n_nights=n_nights,
                )
            sorted_v = sorted(vals)
            p25 = sorted_v[len(sorted_v) // 4]
            p75 = sorted_v[min(len(sorted_v) * 3 // 4, len(sorted_v) - 1)]
            p95 = sorted_v[min(int(len(sorted_v) * 0.95), len(sorted_v) - 1)]
            return DistributionStats(
                median=statistics.median(vals),
                iqr=p75 - p25,
                p95=p95,
                n_breaths=n_breaths,
                n_nights=n_nights,
            )

        requested = set(metrics) if metrics is not None else set(DistributionMetric)
        epoch_stats = []

        for ed in epoch_resolved:
            epoch = ed["epoch"]
            contributing_sessions = ed["contributing_sessions"]
            nights_with_data = ed["nights_with_data"]
            nights_missing_analysis = ed["nights_missing_analysis"]
            all_rx = ed["all_rx"]
            null_reason_ed: NullReason | None = ed["null_reason"]

            if null_reason_ed is not None or not contributing_sessions:
                epoch_stats.append(
                    EpochBreathStats(
                        label=epoch.label,
                        date_start=epoch.date_start,
                        date_end=epoch.date_end,
                        nights_with_data=nights_with_data,
                        nights_missing_analysis=nights_missing_analysis,
                        algorithm_identity=None,
                        null_reason=null_reason_ed or NullReason.NO_DATA_IN_RANGE,
                        primary_mode=None,
                        mid_insp_flattening=_null_dist,
                        flatness_index=_null_dist,
                        flow_class_distribution={},
                        tidal_volume_ml=_null_dist,
                        ie_ratio=_null_dist,
                        rera_proxy_count=None,
                        rera_reason=NullReason.ANALYSIS_NOT_RUN,
                        rx_settings=all_rx[0] if all_rx else {},
                    )
                )
                continue

            all_identities = [algo.identity for _, algo in contributing_sessions]

            # Check primary_mode uniformity for RERA
            all_modes_str = [algo.run.primary_mode for _, algo in contributing_sessions]
            if len(set(all_modes_str)) == 1:
                uniform_primary_mode: str | None = all_modes_str[0]
                rera_reason: NullReason | None = None
            else:
                uniform_primary_mode = None
                rera_reason = NullReason.PRIMARY_MODE_MISMATCH

            ar_ids = ed["ar_ids"]
            contributing_ar_ids: list[tuple[int, int]] = [
                (sid, ar_ids[sid])
                for sid, _ in contributing_sessions
                if ar_ids.get(sid) is not None
            ]

            all_breath_rows: list[Any] = []
            for _sid, ar_id in contributing_ar_ids:
                brows = (
                    (
                        await self._db.execute(
                            select(models.Breath).where(
                                models.Breath.analysis_result_id == ar_id,
                                models.Breath.leak_valid.is_(True),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                all_breath_rows.extend(brows)

            n_lv = len(all_breath_rows)
            n_nights_contrib = nights_with_data

            mif_vals = [
                b.mid_insp_flattening
                for b in all_breath_rows
                if b.mid_insp_flattening is not None
            ]
            fi_vals = [
                b.flatness_index
                for b in all_breath_rows
                if b.flatness_index is not None
            ]
            tv_vals = [
                b.tidal_volume_ml
                for b in all_breath_rows
                if b.tidal_volume_ml is not None
            ]
            ie_vals = [b.i_e_ratio for b in all_breath_rows if b.i_e_ratio is not None]
            fc_dist: dict[int, int] = {}
            for b in all_breath_rows:
                if b.flow_class is not None:
                    fc_dist[b.flow_class] = fc_dist.get(b.flow_class, 0) + 1

            # RERA proxy: FL runs ending in recovery breath
            rera_count: int | None = None
            if uniform_primary_mode is not None:
                rera_count = 0
                for _sid, ar_id in contributing_ar_ids:
                    brows_all = (
                        (
                            await self._db.execute(
                                select(models.Breath)
                                .where(models.Breath.analysis_result_id == ar_id)
                                .order_by(models.Breath.breath_number)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    rera_count += _count_fl_run_reras(brows_all)

            epoch_stats.append(
                EpochBreathStats(
                    label=epoch.label,
                    date_start=epoch.date_start,
                    date_end=epoch.date_end,
                    nights_with_data=nights_with_data,
                    nights_missing_analysis=nights_missing_analysis,
                    algorithm_identity=all_identities[0],
                    null_reason=None,
                    primary_mode=uniform_primary_mode,
                    mid_insp_flattening=_distrib(mif_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.MID_INSP_FLATTENING in requested
                    else _null_dist,
                    flatness_index=_distrib(fi_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.FLATNESS_INDEX in requested
                    else _null_dist,
                    flow_class_distribution=fc_dist,
                    tidal_volume_ml=_distrib(tv_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.TIDAL_VOLUME_ML in requested
                    else _null_dist,
                    ie_ratio=_distrib(ie_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.IE_RATIO in requested
                    else _null_dist,
                    rera_proxy_count=rera_count,
                    rera_reason=rera_reason,
                    rx_settings=all_rx[0] if all_rx else {},
                )
            )

        return CompareEpochsResult(
            epochs=epoch_stats,
            null_reason=None,
            rx_violations=rx_violations,
        )

    async def get_analysis_status(
        self,
        session_id: int,
    ) -> tuple[AnalysisStatus, AlgoVersions | None]:
        """(status, versions) for a session's latest AnalysisResult.

        Returns (NOT_RUN, None) if the session is not owned by this profile.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        # Verify profile ownership before querying analysis result
        owned = (
            await self._db.execute(
                select(models.Session.id)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    models.Session.id == session_id,
                    models.Device.profile_id == self._profile_id,
                )
            )
        ).scalar_one_or_none()
        if owned is None:
            return AnalysisStatus.NOT_RUN, None

        row = (
            (
                await self._db.execute(
                    select(models.AnalysisResult)
                    .where(models.AnalysisResult.session_id == session_id)
                    .order_by(
                        models.AnalysisResult.created_at.desc(),
                        models.AnalysisResult.id.desc(),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return AnalysisStatus.NOT_RUN, None

        stored = row.engine_versions_json
        if not stored or "identity" not in stored:
            return AnalysisStatus.STALE_VERSION, None

        try:
            algo = AlgoVersions.model_validate(stored)
        except Exception:
            return AnalysisStatus.STALE_VERSION, None

        current = self._current_algorithm_identity()
        if algo.identity.model_dump() != current.model_dump():
            return AnalysisStatus.STALE_VERSION, algo

        return AnalysisStatus.OK, algo

    @staticmethod
    def _build_nightly_summary(
        *,
        therapy_date: date,
        device_id: int,
        day_sessions: list[Any],
        day_row: Any | None,
        ar_classification: dict[
            int, tuple[AnalysisStatus, AlgoVersions | None, int | None]
        ],
        breath_rows_by_ar_id: dict[int, list[Any]],
        compliance_threshold_hours: float,
    ) -> NightlyAnalysisSummary:
        """Build a NightlyAnalysisSummary from pre-fetched data. No I/O."""
        import statistics  # noqa: PLC0415

        if day_row is not None and day_row.total_therapy_hours is not None:
            total_therapy_hours = float(day_row.total_therapy_hours)
        else:
            total_therapy_hours = (
                sum(s.duration_seconds or 0.0 for s in day_sessions) / 3600.0
            )

        session_coverages: list[SessionCoverage] = []
        ok_sessions: list[tuple[int, AlgoVersions]] = []
        identities_for_reduce: list[AlgorithmIdentity] = []
        missing_or_stale: list[int] = []
        algo_identity: AlgorithmIdentity | None = None

        for s in day_sessions:
            sid = s.id
            status, algo, _ar_id = ar_classification.get(
                sid, (AnalysisStatus.NOT_RUN, None, None)
            )
            session_coverages.append(
                SessionCoverage(
                    session_id=sid, analysis_status=status, algo_versions=algo
                )
            )
            if status == AnalysisStatus.OK and algo is not None:
                ok_sessions.append((sid, algo))
                identities_for_reduce.append(algo.identity)
                algo_identity = algo.identity
            else:
                missing_or_stale.append(sid)

        eligible = len(day_sessions)
        analyzed = len(ok_sessions)

        day_status = BreathService._reduce_day_status(
            session_coverages, identities_for_reduce
        )
        day_ahi = day_row.ahi if day_row is not None else None

        if not ok_sessions:
            return NightlyAnalysisSummary(
                therapy_date=therapy_date,
                device_id=device_id,
                day_status=day_status,
                session_coverage=session_coverages,
                eligible_session_count=eligible,
                analyzed_session_count=0,
                missing_or_stale_session_ids=missing_or_stale,
                algorithm_identity=None,
                rera_count=None,
                rera_reason=NullReason.NOT_AVAILABLE,
                primary_mode=None,
                fl_median=None,
                fl_95th=None,
                fl_max=None,
                fl_reason=NullReason.NOT_AVAILABLE,
                fl_class_ge4_pct=None,
                fl_class_ge4_pct_reason=NullReason.NOT_AVAILABLE,
                ti_median_s=None,
                ti_median_reason=NullReason.NOT_AVAILABLE,
                ie_ratio_median=None,
                ie_ratio_reason=NullReason.NOT_AVAILABLE,
                total_therapy_hours=total_therapy_hours,
                compliance_threshold_hours=compliance_threshold_hours,
                is_compliant=total_therapy_hours >= compliance_threshold_hours,
                rera_index=None,
                rera_index_reason=NullReason.NOT_AVAILABLE,
                rdi=None,
                rdi_reason=NullReason.NOT_AVAILABLE,
            )

        # MIXED_VERSION within a day is handled by _reduce_day_status; under current
        # _latest_analysis_for_session semantics all OK sessions share the current identity.

        modes_seen = {algo.run.primary_mode for _, algo in ok_sessions}
        uniform_primary_mode = next(iter(modes_seen)) if len(modes_seen) == 1 else None

        fl_vals: list[float] = []
        ti_vals: list[float] = []
        ie_vals: list[float] = []
        fl_class_ge4_num = 0
        fl_class_den = 0
        rera_count = 0

        for sid, _algo in ok_sessions:
            _status, _a, ar_id = ar_classification.get(sid, (None, None, None))
            if ar_id is None:
                continue
            breath_rows = breath_rows_by_ar_id.get(ar_id, [])
            for b in breath_rows:
                if b.leak_valid is True:
                    if b.mid_insp_flattening is not None:
                        fl_vals.append(b.mid_insp_flattening)
                    if b.inspiration_time_s is not None:
                        ti_vals.append(b.inspiration_time_s)
                    if b.i_e_ratio is not None:
                        ie_vals.append(b.i_e_ratio)
                    if b.flow_class is not None:
                        fl_class_den += 1
                        if b.flow_class >= 4:
                            fl_class_ge4_num += 1
            # RERA proxy: FL runs ending in recovery breath.  Intentionally
            # scans ALL breaths (not just leak-valid) — runs need sequence
            # contiguity.
            rera_count += _count_fl_run_reras(breath_rows)

        fl_median: float | None
        fl_95th: float | None
        fl_max: float | None
        fl_reason: NullReason | None

        if fl_vals:
            sorted_fl = sorted(fl_vals)
            n = len(sorted_fl)
            fl_median = float(statistics.median(sorted_fl))
            p95_idx = min(int(n * 0.95), n - 1)
            fl_95th = sorted_fl[p95_idx]
            fl_max = sorted_fl[-1]
            fl_reason = None
        else:
            fl_median = fl_95th = fl_max = None
            fl_reason = NullReason.NOT_AVAILABLE

        fl_class_ge4_pct: float | None
        fl_class_ge4_pct_reason: NullReason | None

        if fl_class_den > 0:
            fl_class_ge4_pct = 100.0 * fl_class_ge4_num / fl_class_den
            fl_class_ge4_pct_reason = None
        else:
            fl_class_ge4_pct = None
            fl_class_ge4_pct_reason = NullReason.NOT_AVAILABLE

        ti_median_s: float | None
        ti_median_reason: NullReason | None
        ie_ratio_median: float | None
        ie_ratio_reason: NullReason | None

        if ti_vals:
            ti_median_s = float(statistics.median(ti_vals))
            ti_median_reason = None
        else:
            ti_median_s = None
            ti_median_reason = NullReason.NOT_AVAILABLE

        if ie_vals:
            ie_ratio_median = float(statistics.median(ie_vals))
            ie_ratio_reason = None
        else:
            ie_ratio_median = None
            ie_ratio_reason = NullReason.NOT_AVAILABLE

        # rera_index / rdi arithmetic (finding 7 — moves into service)
        rera_reason: NullReason | None = (
            None if uniform_primary_mode is not None else NullReason.NOT_AVAILABLE
        )
        final_rera_count = rera_count if uniform_primary_mode is not None else None

        rera_index: float | None
        rera_index_reason: NullReason | None
        rdi: float | None
        rdi_reason: NullReason | None

        if final_rera_count is not None:
            if total_therapy_hours > 0:
                rera_index = round(final_rera_count / total_therapy_hours, 2)
                rera_index_reason = None
            else:
                rera_index = None
                rera_index_reason = NullReason.DURATION_ZERO
        elif rera_reason is not None:
            rera_index = None
            rera_index_reason = rera_reason
        else:
            rera_index = None
            rera_index_reason = None

        if day_ahi is not None and rera_index is not None:
            rdi = round(day_ahi + rera_index, 2)
            rdi_reason = None
        elif rera_index is None and rera_index_reason is not None:
            rdi = None
            rdi_reason = rera_index_reason
        else:
            rdi = None
            rdi_reason = NullReason.NOT_AVAILABLE

        return NightlyAnalysisSummary(
            therapy_date=therapy_date,
            device_id=device_id,
            day_status=day_status,
            session_coverage=session_coverages,
            eligible_session_count=eligible,
            analyzed_session_count=analyzed,
            missing_or_stale_session_ids=missing_or_stale,
            algorithm_identity=algo_identity,
            rera_count=final_rera_count,
            rera_reason=rera_reason,
            primary_mode=uniform_primary_mode,
            fl_median=fl_median,
            fl_95th=fl_95th,
            fl_max=fl_max,
            fl_reason=fl_reason,
            fl_class_ge4_pct=fl_class_ge4_pct,
            fl_class_ge4_pct_reason=fl_class_ge4_pct_reason,
            ti_median_s=ti_median_s,
            ti_median_reason=ti_median_reason,
            ie_ratio_median=ie_ratio_median,
            ie_ratio_reason=ie_ratio_reason,
            total_therapy_hours=total_therapy_hours,
            compliance_threshold_hours=compliance_threshold_hours,
            is_compliant=total_therapy_hours >= compliance_threshold_hours,
            rera_index=rera_index,
            rera_index_reason=rera_index_reason,
            rdi=rdi,
            rdi_reason=rdi_reason,
        )

    async def get_nightly_summary(
        self,
        therapy_date: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyAnalysisSummary:
        """Latest-run analysis fields aggregated across all OK sessions of a day."""
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        # Resolve device (raises ValueError when no sessions, DeviceAmbiguityError when ≥2)
        resolved_device_id, sessions_by_date = await self._resolve_range(
            therapy_date, therapy_date, device_id
        )
        day_sessions = sessions_by_date.get(therapy_date, [])
        if not day_sessions:
            raise ValueError(f"No sessions found for date {therapy_date}")

        day_row = (
            (
                await self._db.execute(
                    select(models.Day).where(
                        models.Day.device_id == resolved_device_id,
                        models.Day.date == therapy_date,
                    )
                )
            )
            .scalars()
            .first()
        )

        # Per-session classification (single-night path keeps per-session queries)
        ar_classification: dict[
            int, tuple[AnalysisStatus, AlgoVersions | None, int | None]
        ] = {}
        for s in day_sessions:
            sid = s.id
            status, algo, ar_id = await self._latest_analysis_for_session(sid)
            ar_classification[sid] = (status, algo, ar_id)

        # Fetch breath rows for OK sessions
        breath_rows_by_ar_id: dict[int, list[Any]] = {}
        for s in day_sessions:
            sid = s.id
            status, _algo, ar_id = ar_classification[sid]
            if status == AnalysisStatus.OK and ar_id is not None:
                breath_rows = (
                    (
                        await self._db.execute(
                            select(models.Breath)
                            .where(models.Breath.analysis_result_id == ar_id)
                            .order_by(models.Breath.breath_number)
                        )
                    )
                    .scalars()
                    .all()
                )
                breath_rows_by_ar_id[ar_id] = list(breath_rows)

        return self._build_nightly_summary(
            therapy_date=therapy_date,
            device_id=resolved_device_id,
            day_sessions=day_sessions,
            day_row=day_row,
            ar_classification=ar_classification,
            breath_rows_by_ar_id=breath_rows_by_ar_id,
            compliance_threshold_hours=compliance_threshold_hours,
        )

    async def get_nightly_range_summary(
        self,
        date_start: date,
        date_end: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyRangeSummary:
        """Per-night summaries + aggregate compliance (bulk-query path)."""
        from datetime import timedelta  # noqa: PLC0415

        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        if date_end < date_start:
            raise ValueError(
                f"date_end ({date_end}) must be >= date_start ({date_start})"
            )

        n_calendar = (date_end - date_start).days + 1

        # Enforce pagination cap (plan Phase 1: "paginated ~30 nights/call").
        _MAX_NIGHTS = 90
        if n_calendar > _MAX_NIGHTS:
            raise ValueError(
                f"Date range spans {n_calendar} nights; maximum per call is "
                f"{_MAX_NIGHTS}. Use multiple calls to page over longer ranges."
            )

        # Resolve device ONCE across the full range.
        # DeviceAmbiguityError and DeviceNotOwnedError propagate (ownership failures).
        # ValueError (no sessions, device_id=None auto-select found nothing) → empty summary.
        try:
            resolved_device_id, sessions_by_date = await self._resolve_range(
                date_start, date_end, device_id
            )
        except (DeviceAmbiguityError, DeviceNotOwnedError):
            raise
        except ValueError:
            return NightlyRangeSummary(
                date_start=date_start,
                date_end=date_end,
                device_id=device_id or 0,
                compliance_threshold_hours=compliance_threshold_hours,
                n_calendar_nights=n_calendar,
                n_nights=0,
                days_compliant=0,
                compliance_pct=0.0,
                nights=[],
            )

        # Bulk Day query for all dates that have sessions
        all_dates = list(sessions_by_date.keys())
        day_rows = (
            (
                await self._db.execute(
                    select(models.Day).where(
                        models.Day.device_id == resolved_device_id,
                        models.Day.date.in_(all_dates),
                    )
                )
            )
            .scalars()
            .all()
        )
        day_by_date: dict[date, Any] = {d.date: d for d in day_rows}

        # Collect all session IDs across the range
        all_sessions: list[Any] = []
        for sessions in sessions_by_date.values():
            all_sessions.extend(sessions)
        all_session_ids = [s.id for s in all_sessions]

        # Bulk AnalysisResult query + Python reduction to latest-per-session
        ar_rows = (
            (
                await self._db.execute(
                    select(models.AnalysisResult)
                    .where(models.AnalysisResult.session_id.in_(all_session_ids))
                    .order_by(
                        models.AnalysisResult.session_id,
                        models.AnalysisResult.created_at.desc(),
                        models.AnalysisResult.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )

        latest_ar_by_session: dict[int, Any] = {}
        for ar in ar_rows:
            if ar.session_id not in latest_ar_by_session:
                latest_ar_by_session[ar.session_id] = ar

        # Classify each session
        ar_classification: dict[
            int, tuple[AnalysisStatus, AlgoVersions | None, int | None]
        ] = {}
        for sid in all_session_ids:
            ar_row = latest_ar_by_session.get(sid)
            if ar_row is None:
                ar_classification[sid] = (AnalysisStatus.NOT_RUN, None, None)
            else:
                status, algo = self._classify_analysis_row(ar_row)
                ar_classification[sid] = (status, algo, ar_row.id)

        # Bulk Breath query for all OK ar_ids (8 columns only)
        ok_ar_ids = [
            ar_id
            for (_status, _algo, ar_id) in ar_classification.values()
            if _status == AnalysisStatus.OK and ar_id is not None
        ]
        breath_rows_by_ar_id: dict[int, list[Any]] = {ar_id: [] for ar_id in ok_ar_ids}

        if ok_ar_ids:
            breath_cols = (
                models.Breath.analysis_result_id,
                models.Breath.breath_number,
                models.Breath.leak_valid,
                models.Breath.mid_insp_flattening,
                models.Breath.inspiration_time_s,
                models.Breath.i_e_ratio,
                models.Breath.flow_class,
                models.Breath.is_recovery_breath,
            )
            breath_result = await self._db.execute(
                select(*breath_cols)
                .where(models.Breath.analysis_result_id.in_(ok_ar_ids))
                .order_by(
                    models.Breath.analysis_result_id,
                    models.Breath.breath_number,
                )
            )
            for row in breath_result:
                breath_rows_by_ar_id[row.analysis_result_id].append(row)

        # Per-night builder loop (nights without sessions are skipped)
        nights: list[NightlyAnalysisSummary] = []
        days_compliant = 0
        current = date_start
        while current <= date_end:
            day_sessions = sessions_by_date.get(current, [])
            if day_sessions:
                summary = self._build_nightly_summary(
                    therapy_date=current,
                    device_id=resolved_device_id,
                    day_sessions=day_sessions,
                    day_row=day_by_date.get(current),
                    ar_classification=ar_classification,
                    breath_rows_by_ar_id=breath_rows_by_ar_id,
                    compliance_threshold_hours=compliance_threshold_hours,
                )
                nights.append(summary)
                if summary.is_compliant:
                    days_compliant += 1
            current += timedelta(days=1)

        n_nights = len(nights)
        compliance_pct = (
            (days_compliant / n_calendar * 100.0) if n_calendar > 0 else 0.0
        )
        return NightlyRangeSummary(
            date_start=date_start,
            date_end=date_end,
            device_id=resolved_device_id,
            compliance_threshold_hours=compliance_threshold_hours,
            n_calendar_nights=n_calendar,
            n_nights=n_nights,
            days_compliant=days_compliant,
            compliance_pct=compliance_pct,
            nights=nights,
        )

    async def get_device_capabilities(
        self,
        device_id: int,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> DeviceCapabilities:
        """Actual covered range + channels, event types, setting keys present."""
        from sqlalchemy import func as sqlfunc  # noqa: PLC0415
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415
        from snore.parsers.register_all import (
            ensure_registered_parsers,  # noqa: PLC0415
        )

        ensure_registered_parsers()

        # Verify device ownership before querying (fetch full row for identity fields)
        owned_device = (
            (
                await self._db.execute(
                    select(models.Device).where(
                        models.Device.id == device_id,
                        models.Device.profile_id == self._profile_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if owned_device is None:
            return DeviceCapabilities(
                device_id=device_id,
                requested_date_start=date_start,
                requested_date_end=date_end,
                actual_date_start=None,
                actual_date_end=None,
                null_reason=NullReason.NOT_AVAILABLE,
                channels_present=[],
                all_setting_keys_present=[],
                rx_keys_present=[],
                event_types_present=[],
                session_count=0,
                nights_with_data=0,
                supported_vendor_models=[],
                manufacturer=None,
                model=None,
                serial_number=None,
            )

        # Date range of actual data — only days with at least one Session count
        # as "imported nights" (plan §13 lines 949-961).
        from sqlalchemy import exists  # noqa: PLC0415

        day_stmt = select(models.Day).where(
            models.Day.device_id == device_id,
            exists().where(models.Session.day_id == models.Day.id),
        )
        if date_start is not None:
            day_stmt = day_stmt.where(models.Day.date >= date_start)
        if date_end is not None:
            day_stmt = day_stmt.where(models.Day.date <= date_end)
        days = (await self._db.execute(day_stmt)).scalars().all()

        null_reason: NullReason | None = None
        actual_start: date | None = None
        actual_end: date | None = None
        session_count = 0
        nights_with_data = 0

        if not days:
            # Owned device exists but has no data in range
            null_reason = NullReason.NO_DATA_IN_RANGE
        else:
            actual_start = min(d.date for d in days)
            actual_end = max(d.date for d in days)
            nights_with_data = len(days)
            day_ids = [d.id for d in days]

            sess_count_row = (
                await self._db.execute(
                    select(sqlfunc.count())
                    .select_from(models.Session)
                    .where(models.Session.day_id.in_(day_ids))
                )
            ).scalar()
            session_count = sess_count_row or 0

        # Session IDs for this device in range
        sess_stmt = (
            select(models.Session.id)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(models.Day.device_id == device_id)
        )
        if date_start is not None:
            sess_stmt = sess_stmt.where(models.Day.date >= date_start)
        if date_end is not None:
            sess_stmt = sess_stmt.where(models.Day.date <= date_end)
        session_ids = list((await self._db.execute(sess_stmt)).scalars().all())

        channels_present: list[str] = []
        event_types_present: list[str] = []
        all_setting_keys: list[str] = []

        if session_ids:
            wf_rows = (
                (
                    await self._db.execute(
                        select(models.Waveform.waveform_type)
                        .where(models.Waveform.session_id.in_(session_ids))
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            channels_present = sorted(set(str(w) for w in wf_rows))

            ev_rows = (
                (
                    await self._db.execute(
                        select(models.Event.event_type)
                        .where(models.Event.session_id.in_(session_ids))
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            event_types_present = sorted(set(str(e) for e in ev_rows))

            setting_rows = (
                (
                    await self._db.execute(
                        select(models.Setting.key)
                        .where(models.Setting.session_id.in_(session_ids))
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            all_setting_keys = sorted(set(str(k) for k in setting_rows))

        # rx_keys_present: only keys that actually have non-null values (plan §11)
        from snore.analysis.rx_tracker import RX_KEYS as _RX_KEYS  # noqa: PLC0415

        rx_keys: list[str] = []
        if session_ids:
            rx_key_rows = (
                (
                    await self._db.execute(
                        select(models.Setting.key)
                        .where(
                            models.Setting.session_id.in_(session_ids),
                            models.Setting.key.in_(list(_RX_KEYS)),
                            models.Setting.value.is_not(None),
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            rx_keys = sorted(set(str(k) for k in rx_key_rows))

        # Supported vendor models from parsers registry — let real exceptions propagate
        from snore.parsers.registry import parser_registry  # noqa: PLC0415

        supported_models: list[str] = list(parser_registry.list_supported_models())

        return DeviceCapabilities(
            device_id=device_id,
            requested_date_start=date_start,
            requested_date_end=date_end,
            actual_date_start=actual_start,
            actual_date_end=actual_end,
            null_reason=null_reason,
            channels_present=channels_present,
            all_setting_keys_present=all_setting_keys,
            rx_keys_present=rx_keys,
            event_types_present=event_types_present,
            session_count=session_count,
            nights_with_data=nights_with_data,
            supported_vendor_models=supported_models,
            manufacturer=owned_device.manufacturer,
            model=owned_device.model,
            serial_number=owned_device.serial_number,
        )

    async def get_contextual_events(
        self,
        therapy_date: date,
        event_types: list[str] | None = None,
        min_duration: float | None = None,
        device_id: int | None = None,
    ) -> list[ContextualEvent]:
        """Machine events enriched with waveform context.

        Returns events from ALL sessions on the resolved device.
        Pressure and leak values are sampled at the event start (±5 s window).
        MV is the mean over the 120 s preceding the event.
        All values are ``null`` + ``NOT_AVAILABLE`` when the relevant channel is absent.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        # Input validation
        if event_types is not None:
            if not isinstance(event_types, list) or not all(
                isinstance(et, str) and et for et in event_types
            ):
                raise ValueError(
                    "event_types must be None or a list of non-empty strings"
                )
            # Deduplicate (order-preserving), then enforce the 50-item cap (plan §13).
            event_types = list(dict.fromkeys(event_types))
            if len(event_types) > 50:
                raise ValueError("event_types must contain at most 50 unique values")
        if min_duration is not None and min_duration < 0:
            raise ValueError("min_duration must be None or >= 0")

        # Resolve device via _resolve_range — DeviceAmbiguityError and ownership
        # errors propagate to the caller; a foreign/unknown device is not []
        resolved_device_id, sessions_by_date = await self._resolve_range(
            therapy_date, therapy_date, device_id
        )
        day_sessions = sessions_by_date.get(therapy_date, [])

        results: list[ContextualEvent] = []
        for session_row in day_sessions:
            session_id = session_row.id
            session_start = session_row.start_time
            session_start_f = session_start.timestamp()

            # Fetch machine events for this session
            ev_stmt = select(models.Event).where(models.Event.session_id == session_id)
            if event_types:
                ev_stmt = ev_stmt.where(models.Event.event_type.in_(event_types))
            if min_duration is not None:
                ev_stmt = ev_stmt.where(models.Event.duration_seconds >= min_duration)
            ev_stmt = ev_stmt.order_by(models.Event.start_time)
            events = (await self._db.execute(ev_stmt)).scalars().all()

            # Pre-load all needed channels for this session ONCE — one DB fetch for
            # all events rather than two per event (fix: per-event blob read N+1).
            # Corrupt blobs still raise ValueError per plan IMPORTANT-8.
            session_duration_s = session_row.duration_seconds or 32400.0
            pre_raw = await _fetch_waveform_blobs(
                self._db,
                WaveformWindowRequest(
                    therapy_date=therapy_date,
                    session_id=session_id,
                    device_id=resolved_device_id,
                    channels=[
                        WaveformChannelName.PRESSURE,
                        WaveformChannelName.LEAK,
                        WaveformChannelName.MV,
                    ],
                    offset_start=0.0,
                    offset_end=session_duration_s,
                    window_cap_seconds=session_duration_s,
                ),
                session_id,
                session_start,
            )
            pre_window = compute_waveform_window(pre_raw)
            # Map channel_type → (offsets, values) for O(1) per-event slicing
            pre_ch: dict[WaveformChannelName, tuple[list[float], list[float]]] = {
                ch.channel_type: (ch.offset_seconds, ch.values)
                for ch in pre_window.channels
            }

            for ev in events:
                ev_start_f = ev.start_time.timestamp()
                offset_s = ev_start_f - session_start_f
                minutes_since = offset_s / 60.0

                pressure_at: float | None = None
                pressure_reason: NullReason | None = NullReason.NOT_AVAILABLE
                leak_at: float | None = None
                leak_reason: NullReason | None = NullReason.NOT_AVAILABLE
                mv_prior: float | None = None
                mv_reason: NullReason | None = NullReason.NOT_AVAILABLE

                window_start = max(0.0, offset_s - 5.0)
                window_end = offset_s + 5.0
                mv_window_start = max(0.0, offset_s - 120.0)

                # Slice pre-loaded arrays — no DB access per event.
                # Guard window_end > 0 (fix: event before session start crash).
                if window_end > 0.0:
                    for ch_type in (
                        WaveformChannelName.PRESSURE,
                        WaveformChannelName.LEAK,
                    ):
                        if ch_type in pre_ch:
                            offsets, vals = pre_ch[ch_type]
                            val = _extract_window_mean(
                                offsets, vals, window_start, window_end
                            )
                            if val is not None:
                                if ch_type == WaveformChannelName.PRESSURE:
                                    pressure_at = val
                                    pressure_reason = None
                                else:
                                    leak_at = val
                                    leak_reason = None

                # MV window (prior 120 s)
                if offset_s > 0.0 and WaveformChannelName.MV in pre_ch:
                    offsets, vals = pre_ch[WaveformChannelName.MV]
                    val = _extract_window_mean(offsets, vals, mv_window_start, offset_s)
                    if val is not None:
                        mv_prior = val
                        mv_reason = None

                results.append(
                    ContextualEvent(
                        session_id=session_id,
                        session_start_wall_clock=session_start,
                        event_type=ev.event_type,
                        event_start_wall_clock=ev.start_time,
                        timezone_status=TimezoneStatus.UNKNOWN,
                        offset_seconds=offset_s,
                        duration_seconds=ev.duration_seconds,
                        pressure_at_event_cmh2o=pressure_at,
                        pressure_reason=pressure_reason,
                        leak_at_event_lpm=leak_at,
                        leak_reason=leak_reason,
                        mv_prior_120s_lpm=mv_prior,
                        mv_reason=mv_reason,
                        minutes_since_session_start=minutes_since,
                    )
                )
        return results

    async def fetch_waveform_window(
        self, request: WaveformWindowRequest
    ) -> RawWaveformWindow:
        """Resolve, validate, and fetch raw waveform blobs for a window request.

        MCP raw/render seam (plan docs/mcp-server-plan.md §9): the fetch step runs
        inside the caller's DB scope while ``compute_waveform_window`` (pure, CPU-only)
        runs after the scope closes.  Direct callers that need the raw bytes or want
        to render a PNG call this method, then pass the returned ``RawWaveformWindow``
        to ``compute_waveform_window`` independently.

        Raises ``DeviceAmbiguityError`` for multi-device profiles with no device_id,
        ``DeviceNotOwnedError`` for a foreign device_id, ``ValueError`` when an
        explicit session_id is provided but the date has no sessions, and
        ``MultiSessionAmbiguityError`` when the date has multiple sessions and no
        session_id was specified.
        """
        resolved_device_id, sessions_by_date = await self._resolve_range(
            request.therapy_date, request.therapy_date, request.device_id
        )
        day_sessions = sessions_by_date.get(request.therapy_date, [])

        # Validate explicit session_id BEFORE the empty-day return.
        # An owned device on an empty date with an explicit session_id must raise,
        # not silently return a synthetic empty window (plan §9 lines 822-825).
        if not day_sessions:
            if request.session_id is not None:
                raise ValueError(
                    f"Session {request.session_id} not found for date "
                    f"{request.therapy_date} on device {resolved_device_id}"
                )
            return RawWaveformWindow(
                request=request,
                session_id=0,
                session_start_wall_clock=datetime.min,
                channels=[],
                missing_channels=list(request.channels),
            )

        if request.session_id is not None:
            # Verify the provided session_id belongs to the resolved device
            session_ids = {s.id for s in day_sessions}
            if request.session_id not in session_ids:
                raise ValueError(
                    f"Session {request.session_id} not found for date "
                    f"{request.therapy_date} on device {resolved_device_id}"
                )
            session_row = next(s for s in day_sessions if s.id == request.session_id)
        elif len(day_sessions) > 1:
            raise MultiSessionAmbiguityError(
                therapy_date=request.therapy_date,
                device_id=resolved_device_id,
                sessions=[
                    SessionSummary(
                        session_id=s.id,
                        start_wall_clock=s.start_time,
                        duration_seconds=s.duration_seconds or 0.0,
                    )
                    for s in day_sessions
                ],
            )
        else:
            session_row = day_sessions[0]

        resolved_request = request.model_copy(
            update={"device_id": resolved_device_id, "session_id": session_row.id}
        )
        return await _fetch_waveform_blobs(
            self._db, resolved_request, session_row.id, session_row.start_time
        )

    async def get_waveform_window(
        self, request: WaveformWindowRequest
    ) -> WaveformWindow:
        """Convenience orchestrator: resolve → fetch blobs → compute. Never closes self._db.

        Uses ``_resolve_range`` for device validation and session selection (raises
        ``DeviceAmbiguityError`` for multi-device, ``DeviceNotOwnedError`` for foreign
        device_id), then delegates to ``fetch_waveform_window`` (the MCP seam) and
        applies ``compute_waveform_window`` (pure) to produce the final DTO.
        """
        raw = await self.fetch_waveform_window(request)
        return compute_waveform_window(raw)

    async def fetch_ca_analysis(
        self, therapy_date: date, device_id: int | None = None
    ) -> RawCaAnalysis:
        """Fetch all DB data for CA analysis (in-scope fetch seam).

        Resolves device, iterates sessions, pre-fetches MV/THERAPY_PRESSURE/EPAP
        waveform blobs (one fetch per session), queries CA events, and loads
        OK-session programmatic_result_json for PB% computation.

        Returns a ``RawCaAnalysis`` carrying every input that
        ``compute_ca_analysis`` needs — no ORM handles or DB sessions escape.
        Empty session_data signals an empty day; ``compute_ca_analysis`` maps it
        to the NOT_RUN sentinel result.

        ``DeviceAmbiguityError`` and ``DeviceNotOwnedError`` propagate to the
        caller unchanged.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        # Resolve device via _resolve_range (DeviceAmbiguityError propagates to caller)
        resolved_device_id, sessions_by_date = await self._resolve_range(
            therapy_date, therapy_date, device_id
        )
        all_day_sessions = sessions_by_date.get(therapy_date, [])

        if not all_day_sessions:
            return RawCaAnalysis(
                therapy_date=therapy_date,
                device_id=resolved_device_id,
                session_data=[],
                day_status=DayAnalysisStatus.NOT_RUN,
                algorithm_identity=None,
                null_reason=NullReason.ANALYSIS_NOT_RUN,
            )

        # Build coverage; identify OK sessions
        coverage: list[SessionCoverage] = []
        identities_for_reduce: list[AlgorithmIdentity] = []
        ok_session_ids: set[int] = set()
        ar_id_by_session: dict[int, int] = {}
        algo_identity: AlgorithmIdentity | None = None

        for sess in all_day_sessions:
            status, algo, ar_id = await self._latest_analysis_for_session(sess.id)
            cov = SessionCoverage(
                session_id=sess.id, analysis_status=status, algo_versions=algo
            )
            coverage.append(cov)
            if status == AnalysisStatus.OK and algo is not None and ar_id is not None:
                ok_session_ids.add(sess.id)
                ar_id_by_session[sess.id] = ar_id
                identities_for_reduce.append(algo.identity)
                algo_identity = algo.identity

        ca_day_status = self._reduce_day_status(coverage, identities_for_reduce)

        # plan §12 lines 984-993: MIXED_VERSION must return algorithm_identity=None
        if ca_day_status == DayAnalysisStatus.MIXED_VERSION:
            algo_identity = None

        # Map day_status → null_reason for the result
        if ca_day_status == DayAnalysisStatus.OK:
            ca_null_reason: NullReason | None = None
        elif ca_day_status == DayAnalysisStatus.STALE:
            ca_null_reason = NullReason.ANALYSIS_STALE
        elif ca_day_status == DayAnalysisStatus.MIXED_VERSION:
            # plan §12 lines 984-993: conflicting algo identities → ALGO_VERSION_MISMATCH
            ca_null_reason = NullReason.ALGO_VERSION_MISMATCH
        elif ca_day_status == DayAnalysisStatus.PARTIAL:
            ca_null_reason = None
        else:
            ca_null_reason = NullReason.ANALYSIS_NOT_RUN

        night_level_refused = ca_day_status == DayAnalysisStatus.MIXED_VERSION

        # Coverage lookup for DTO construction
        cov_by_session: dict[int, SessionCoverage] = {c.session_id: c for c in coverage}

        # Fetch per-session data
        session_data: list[RawCaSessionData] = []
        for session_row in all_day_sessions:
            session_id = session_row.id
            session_start = session_row.start_time
            session_duration_s = session_row.duration_seconds or 0.0
            is_ok = session_id in ok_session_ids

            # Pre-fetch MV + THERAPY_PRESSURE + EPAP blobs once per session.
            # Corrupt blobs still raise ValueError per plan IMPORTANT-8.
            session_cap = max(session_duration_s, 1.0)
            pre_raw = await _fetch_waveform_blobs(
                self._db,
                WaveformWindowRequest(
                    therapy_date=therapy_date,
                    session_id=session_id,
                    device_id=resolved_device_id,
                    channels=[
                        WaveformChannelName.MV,
                        WaveformChannelName.THERAPY_PRESSURE,
                        WaveformChannelName.EPAP,
                    ],
                    offset_start=0.0,
                    offset_end=session_cap,
                    window_cap_seconds=session_cap,
                ),
                session_id,
                session_start,
            )

            # No device MV channel → fetch FLOW blobs for the flow-derived MV
            # fallback (compute_ca_analysis runs derive_mv_from_flow on them).
            flow_raw: RawWaveformWindow | None = None
            if WaveformChannelName.MV in pre_raw.missing_channels:
                flow_raw = await _fetch_waveform_blobs(
                    self._db,
                    WaveformWindowRequest(
                        therapy_date=therapy_date,
                        session_id=session_id,
                        device_id=resolved_device_id,
                        channels=[WaveformChannelName.FLOW],
                        offset_start=0.0,
                        offset_end=session_cap,
                        window_cap_seconds=session_cap,
                    ),
                    session_id,
                    session_start,
                )

            # Fetch CA events for this session
            ca_rows = (
                (
                    await self._db.execute(
                        select(models.Event)
                        .where(
                            models.Event.session_id == session_id,
                            models.Event.event_type == "CA",
                        )
                        .order_by(models.Event.start_time)
                    )
                )
                .scalars()
                .all()
            )
            raw_events = [
                RawCaEvent(
                    start_time=ev.start_time,
                    duration_seconds=ev.duration_seconds,
                )
                for ev in ca_rows
            ]

            # Load programmatic_result_json for OK sessions (PB% computation)
            pb_json: dict[str, Any] | None = None
            if is_ok and not night_level_refused:
                ar_id = ar_id_by_session.get(session_id)
                if ar_id is not None:
                    ar_row = (
                        (
                            await self._db.execute(
                                select(models.AnalysisResult).where(
                                    models.AnalysisResult.id == ar_id
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if ar_row is not None and ar_row.programmatic_result_json:
                        pb_json = ar_row.programmatic_result_json

            session_data.append(
                RawCaSessionData(
                    session_id=session_id,
                    session_start=session_start,
                    duration_seconds=session_duration_s,
                    coverage=cov_by_session[session_id],
                    is_ok=is_ok,
                    pre_waveform=pre_raw,
                    flow_waveform=flow_raw,
                    ca_events=raw_events,
                    pb_json=pb_json,
                )
            )

        return RawCaAnalysis(
            therapy_date=therapy_date,
            device_id=resolved_device_id,
            session_data=session_data,
            day_status=ca_day_status,
            algorithm_identity=algo_identity,
            null_reason=ca_null_reason,
        )

    async def get_ca_analysis(
        self, therapy_date: date, device_id: int | None = None
    ) -> CaAnalysisResult:
        """Convenience orchestrator: fetch CA data → compute CA analysis. Never closes self._db.

        Uses ``fetch_ca_analysis`` (in-scope) to collect all DB data, then applies
        ``compute_ca_analysis`` (pure) to produce the final ``CaAnalysisResult``.
        See ``compute_ca_analysis`` for the numpy/statistics implementation.
        """
        raw = await self.fetch_ca_analysis(
            therapy_date=therapy_date, device_id=device_id
        )
        return compute_ca_analysis(raw)

    @staticmethod
    def _current_algorithm_identity() -> AlgorithmIdentity:
        """Current algorithm identity for STALE_VERSION detection."""
        return AlgorithmIdentity.current()


# ---------------------------------------------------------------------------
# §12 — compute_ca_analysis (module-level pure function)
# ---------------------------------------------------------------------------


def derive_mv_from_flow(
    offsets: np.ndarray,
    values: np.ndarray,
    *,
    window_s: float = 60.0,
    out_dt_s: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Pure — derive minute ventilation (L/min) from a flow waveform (L/min).

    MV(t) = mean of positive-clipped flow over the trailing window
    ``[t - window_s, t]``, sampled every ``out_dt_s`` seconds starting at
    ``offsets[0] + window_s`` up to the last input offset.  Output samples
    whose window contains zero input samples are omitted — merged sessions
    have timestamp gaps, so uniform sampling is never assumed.

    Returns ``(out_offsets, out_values)``; empty arrays when the input is too
    short to cover a single window.  O(n log n): cumsum + searchsorted, no
    per-window scans.
    """
    import numpy as np  # noqa: PLC0415

    if offsets.size == 0 or float(offsets[-1]) - float(offsets[0]) < window_s:
        return np.array([]), np.array([])

    clipped = np.clip(values, 0.0, None)
    csum = np.concatenate(([0.0], np.cumsum(clipped, dtype=np.float64)))

    out_times = np.arange(
        float(offsets[0]) + window_s, float(offsets[-1]) + 1e-9, out_dt_s
    )
    # Window [t - window_s, t] inclusive both ends
    lo = np.searchsorted(offsets, out_times - window_s, side="left")
    hi = np.searchsorted(offsets, out_times, side="right")
    counts = hi - lo
    mask = counts > 0
    mv = (csum[hi[mask]] - csum[lo[mask]]) / counts[mask]
    return out_times[mask], mv


def compute_ca_analysis(raw: RawCaAnalysis) -> CaAnalysisResult:
    """Pure — no DB access. Runs numpy/statistics on pre-fetched raw CA data.

    Deserializes waveform blobs via ``compute_waveform_window``, slices
    per-event windows with searchsorted, computes MV slope/stability/PS,
    accumulates cross-session MV bin means, and derives PB% and rolling
    MV variance.

    Empty ``raw.session_data`` (signalling an empty day) is mapped to a
    NOT_RUN ``CaAnalysisResult`` sentinel consistent with ``get_ca_analysis``.
    """
    import statistics  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    if not raw.session_data:
        return CaAnalysisResult(
            query_date=raw.therapy_date,
            device_id=raw.device_id,
            day_status=raw.day_status,
            session_coverage=[],
            algorithm_identity=raw.algorithm_identity,
            null_reason=raw.null_reason,
            ca_events=[],
            periodic_breathing_pct=None,
            pb_reason=NullReason.NOT_AVAILABLE,
            mv_rolling_variance=None,
            mv_variance_reason=NullReason.NOT_AVAILABLE,
        )

    coverage = [sd.coverage for sd in raw.session_data]
    night_level_refused = raw.day_status == DayAnalysisStatus.MIXED_VERSION

    # Helper: linear regression slope (rise/run), returns L/min per SECOND
    def _mv_slope(xs: list[float], ys: list[float]) -> float | None:
        n = len(xs)
        if n < 2:
            return None
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
        den = sum((x - x_mean) ** 2 for x in xs)
        return num / den if den != 0.0 else None

    ca_details: list[CaDetail] = []
    total_pb_s = 0.0
    total_eligible_s = 0.0
    # True when PB detection ran for ≥1 OK session (pb_json persisted) — zero
    # episodes on an analyzed night is a real 0.0 %, not "not_available".
    pb_ran_any = False
    # Combined MV bin means from ALL OK sessions for cross-session variance
    combined_bin_means: list[float] = []
    mv_rolling_var: float | None = None
    mv_var_reason: NullReason | None = NullReason.NOT_AVAILABLE
    # MV provenance per contributing session ("device" / "flow_derived")
    mv_sources_seen: set[str] = set()

    for sd in raw.session_data:
        session_start_f = sd.session_start.timestamp()

        # Deserialize pre-fetched waveform blobs → numpy arrays (mirrors get_ca_analysis).
        # compute_waveform_window deserializes once per channel; converting to ndarray
        # here enables O(log n) per-event slicing via searchsorted.
        pre_window = compute_waveform_window(sd.pre_waveform)
        pre_ch: dict[WaveformChannelName, tuple[np.ndarray, np.ndarray]] = {
            ch.channel_type: (
                np.array(ch.offset_seconds),
                np.array(ch.values),
            )
            for ch in pre_window.channels
        }

        # MV fallback: no device MV channel → derive MV from the flow waveform
        # and insert it under the MV key so all downstream code (slope,
        # stability, rolling variance) works unchanged.
        mv_source: str | None = None
        if WaveformChannelName.MV in pre_ch:
            mv_source = "device"
        elif sd.flow_waveform is not None:
            flow_window = compute_waveform_window(sd.flow_waveform)
            for flow_ch in flow_window.channels:
                if flow_ch.channel_type == WaveformChannelName.FLOW:
                    mv_off, mv_val = derive_mv_from_flow(
                        np.array(flow_ch.offset_seconds),
                        np.array(flow_ch.values),
                    )
                    if mv_off.size > 0:
                        pre_ch[WaveformChannelName.MV] = (mv_off, mv_val)
                        mv_source = "flow_derived"
                    break
        if mv_source is not None:
            mv_sources_seen.add(mv_source)

        for raw_ev in sd.ca_events:
            ev_start_f = raw_ev.start_time.timestamp()
            offset_s = ev_start_f - session_start_f

            # --- preceding_mv_slope + stability_index ---
            # Window: prior 60 s (plan §12 line 976: stability uses 60-second window)
            preceding_mv_slope: float | None = None
            preceding_mv_reason: NullReason | None = NullReason.NOT_AVAILABLE
            stability_index: float | None = None
            stability_reason: NullReason | None = NullReason.NOT_AVAILABLE

            if offset_s > 0.0 and WaveformChannelName.MV in pre_ch:
                mv_win_start = max(0.0, offset_s - 60.0)
                off_mv, val_mv = pre_ch[WaveformChannelName.MV]
                # searchsorted: O(log n) per event, inclusive both ends
                lo = int(np.searchsorted(off_mv, mv_win_start, side="left"))
                hi = int(np.searchsorted(off_mv, offset_s, side="right"))
                ts_slice = off_mv[lo:hi]
                v_slice = val_mv[lo:hi]
                if len(ts_slice) >= 2:
                    # plan §12 line 976: slope in L/min per MINUTE
                    # _mv_slope returns L/min per SECOND (offset_seconds as x)
                    slope_per_s = _mv_slope(ts_slice.tolist(), v_slice.tolist())
                    if slope_per_s is not None:
                        # convert: multiply by 60 s/min → L/min per minute
                        preceding_mv_slope = slope_per_s * 60.0
                    preceding_mv_reason = (
                        None
                        if preceding_mv_slope is not None
                        else NullReason.NOT_AVAILABLE
                    )
                    if len(ts_slice) >= 3:
                        mean_mv = float(v_slice.mean())
                        if mean_mv != 0.0:
                            stability_index = (
                                statistics.stdev(v_slice.tolist()) / mean_mv
                            )
                            stability_reason = None

            # --- ps_delivered_cmh2o: mean(THERAPY_PRESSURE - EPAP) over ±5 s ---
            ps_delivered: float | None = None
            ps_reason: NullReason | None = NullReason.NOT_AVAILABLE

            ps_win_start = max(0.0, offset_s - 5.0)
            ps_win_end = offset_s + 5.0
            if ps_win_end > 0.0:
                if (
                    WaveformChannelName.THERAPY_PRESSURE in pre_ch
                    and WaveformChannelName.EPAP in pre_ch
                ):
                    off_tp, val_tp = pre_ch[WaveformChannelName.THERAPY_PRESSURE]
                    off_ep, val_ep = pre_ch[WaveformChannelName.EPAP]
                    # searchsorted: O(log n) per event, inclusive both ends
                    tp_lo = int(np.searchsorted(off_tp, ps_win_start, side="left"))
                    tp_hi = int(np.searchsorted(off_tp, ps_win_end, side="right"))
                    ep_lo = int(np.searchsorted(off_ep, ps_win_start, side="left"))
                    ep_hi = int(np.searchsorted(off_ep, ps_win_end, side="right"))
                    tp_slice = val_tp[tp_lo:tp_hi]
                    ep_slice = val_ep[ep_lo:ep_hi]
                    if len(tp_slice) > 0 and len(ep_slice) > 0:
                        min_len = min(len(tp_slice), len(ep_slice))
                        ps_delivered = float(
                            np.mean(tp_slice[:min_len] - ep_slice[:min_len])
                        )
                        ps_reason = None

            ca_details.append(
                CaDetail(
                    session_id=sd.session_id,
                    session_start_wall_clock=sd.session_start,
                    timezone_status=TimezoneStatus.UNKNOWN,
                    offset_seconds=offset_s,
                    duration_seconds=raw_ev.duration_seconds,
                    preceding_mv_slope=preceding_mv_slope,
                    preceding_mv_reason=preceding_mv_reason,
                    ps_delivered_cmh2o=ps_delivered,
                    ps_reason=ps_reason,
                    stability_index=stability_index,
                    stability_reason=stability_reason,
                    mv_source=mv_source,
                )
            )

        # Night-level metrics: OK sessions ONLY (eligibility gate)
        if sd.is_ok and not night_level_refused:
            total_eligible_s += sd.duration_seconds

            # PB% from persisted AnalysisResult JSON
            if sd.pb_json is not None:
                from snore.analysis.types import (
                    AnalysisResult as AnalysisResultDTO,  # noqa: PLC0415
                )

                pb_ran_any = True
                dto = AnalysisResultDTO.model_validate(sd.pb_json)
                for ep in dto.periodic_breathing_episodes or []:
                    start_t = float(ep.get("start_time", ep.get("start", 0)))
                    end_t = float(
                        ep.get(
                            "end_time",
                            ep.get("end", start_t + ep.get("duration", 0)),
                        )
                    )
                    total_pb_s += max(0.0, end_t - start_t)

            # MV rolling variance: collect bin means across ALL OK sessions
            # (combined; variance computed once after the loop).
            # Vectorized with numpy: one searchsorted pass per bin rather than
            # a full-list comprehension, and max() hoisted out of the loop.
            if WaveformChannelName.MV in pre_ch:
                ts_arr, v_arr = pre_ch[WaveformChannelName.MV]
                if ts_arr.size >= 6:
                    max_t = float(ts_arr.max())
                    bin_size = 600.0
                    for bin_start in np.arange(0.0, max_t, bin_size):
                        bin_end = float(bin_start) + bin_size
                        lo = int(np.searchsorted(ts_arr, bin_start, side="left"))
                        hi = int(np.searchsorted(ts_arr, bin_end, side="left"))
                        if lo < hi:
                            combined_bin_means.append(float(v_arr[lo:hi].mean()))

    # Compute cross-session MV variance from combined bin means (OK sessions only)
    if not night_level_refused and len(combined_bin_means) >= 2:
        mv_rolling_var = statistics.variance(combined_bin_means)
        mv_var_reason = None

    # Compute pb_pct over eligible (OK) sessions only
    pb_pct: float | None = None
    pb_reason: NullReason | None = NullReason.NOT_AVAILABLE
    if night_level_refused:
        pb_reason = NullReason.ALGO_VERSION_MISMATCH
        mv_var_reason = NullReason.ALGO_VERSION_MISMATCH
    elif pb_ran_any and total_eligible_s > 0:
        # PB detection ran → zero episodes is a genuine 0.0 %, not null.
        # total_eligible_s == 0 (NULL session durations) stays null+NOT_AVAILABLE.
        pb_pct = total_pb_s / total_eligible_s * 100.0
        pb_reason = None

    # Aggregate MV provenance across contributing sessions
    if not mv_sources_seen:
        night_mv_source: str | None = None
    elif len(mv_sources_seen) == 1:
        night_mv_source = next(iter(mv_sources_seen))
    else:
        night_mv_source = "mixed"

    return CaAnalysisResult(
        query_date=raw.therapy_date,
        device_id=raw.device_id,
        day_status=raw.day_status,
        session_coverage=coverage,
        algorithm_identity=raw.algorithm_identity,
        null_reason=raw.null_reason,
        ca_events=ca_details,
        periodic_breathing_pct=pb_pct,
        pb_reason=pb_reason,
        mv_rolling_variance=mv_rolling_var,
        mv_variance_reason=mv_var_reason,
        mv_source=night_mv_source,
    )
