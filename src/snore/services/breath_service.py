"""BreathService — query layer over the breaths table.

All types in this module are the Appendix-A typed seam definitions (plan v3.8).
PR-B (Duncan) consumes these seams; PR-A (this PR) defines and implements them.

All types live here per Appendix A §13 note ("All types live in
src/snore/services/breath_service.py").
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.shared.versioning import (
    CROSS_VERSION_REFUSAL_KEYS,
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
    # Functions
    "fetch_waveform_window_raw",
    "compute_waveform_window",
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
    """One row from the breaths table."""

    analysis_result_id: int
    session_id: int
    breath_number: int

    session_start_wall_clock: datetime  # naive — tier-2
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    start_offset_seconds: float
    end_offset_seconds: float

    # Timing
    ti: float
    te: float
    ttot: float
    ie_ratio: float
    duty_cycle: float

    # Amplitude
    peak_insp_flow: float  # L/min
    peak_exp_flow: float  # L/min
    tidal_volume: float  # mL

    # Flow limitation features
    flatness_index: float
    mid_insp_flattening: float

    # Classification
    flow_class: int
    flow_class_confidence: float
    is_recovery_breath: bool

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


async def fetch_waveform_window_raw(
    db: AsyncSession,
    request: WaveformWindowRequest,
) -> RawWaveformWindow:
    """DB I/O ONLY — fetch waveform blobs for the requested channels.

    Never closes db: the scope owner opens and closes the scope around this call.
    """

    from sqlalchemy import select  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415

    # Resolve session_id from therapy_date + device_id
    stmt = (
        select(models.Session, models.Day)
        .join(models.Day, models.Session.day_id == models.Day.id)
        .where(models.Day.date == request.therapy_date)
    )
    if request.device_id is not None:
        stmt = stmt.where(models.Session.device_id == request.device_id)
    if request.session_id is not None:
        stmt = stmt.where(models.Session.id == request.session_id)

    rows = (await db.execute(stmt)).all()
    if not rows:
        # Return empty window
        return RawWaveformWindow(
            request=request,
            session_id=request.session_id or 0,
            session_start_wall_clock=datetime.min,
            channels=[],
            missing_channels=list(request.channels),
        )

    if len(rows) > 1 and request.session_id is None:
        sessions_list = [
            SessionSummary(
                session_id=r.Session.id,
                start_wall_clock=r.Session.start_time,
                duration_seconds=r.Session.duration_seconds or 0.0,
            )
            for r in rows
        ]
        raise MultiSessionAmbiguityError(
            therapy_date=request.therapy_date,
            device_id=request.device_id or rows[0].Session.device_id,
            sessions=sessions_list,
        )

    session_row = rows[0].Session
    session_id = session_row.id

    # Fetch waveform blobs
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
        session_start_wall_clock=session_row.start_time,
        channels=channels,
        missing_channels=missing,
    )


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
        try:
            if raw_ch.sample_count <= 0 or not raw_ch.raw_bytes:
                missing_channels.append(raw_ch.waveform_type)
                continue
            timestamps, values = deserialize_waveform_blob(
                raw_ch.raw_bytes, raw_ch.sample_count
            )
            # Slice to requested window
            mask = (timestamps >= request.offset_start) & (
                timestamps <= request.offset_end
            )
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
        except Exception:
            # Missing/corrupt channel → move to missing list
            missing_channels.append(raw_ch.waveform_type)

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

    total_therapy_hours: float
    compliance_threshold_hours: float
    is_compliant: bool


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


# ---------------------------------------------------------------------------
# §13 — BreathService
# ---------------------------------------------------------------------------


class BreathService:
    """Query layer over the breaths table. All methods are async."""

    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_session_for_date(
        self, therapy_date: date, device_id: int | None
    ) -> tuple[int, int]:
        """Return (session_id, device_id) for a single-session therapy day.

        Raises MultiSessionAmbiguityError when the day has >1 session and
        device_id didn't uniquely identify one.  Raises ValueError when
        no session exists for the date.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

        stmt = (
            select(models.Session, models.Day)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(models.Day.date == therapy_date)
        )
        if device_id is not None:
            stmt = stmt.where(models.Session.device_id == device_id)
        rows = (await self._db.execute(stmt)).all()
        if not rows:
            raise ValueError(
                f"No session found for date {therapy_date}"
                + (f" device_id={device_id}" if device_id is not None else "")
            )
        if len(rows) > 1:
            sessions_list = [
                SessionSummary(
                    session_id=r.Session.id,
                    start_wall_clock=r.Session.start_time,
                    duration_seconds=r.Session.duration_seconds or 0.0,
                )
                for r in rows
            ]
            raise MultiSessionAmbiguityError(
                therapy_date=therapy_date,
                device_id=device_id or rows[0].Session.device_id,
                sessions=sessions_list,
            )
        row = rows[0]
        return row.Session.id, row.Session.device_id

    async def _latest_analysis_for_session(
        self, session_id: int
    ) -> tuple[AnalysisStatus, AlgoVersions | None, int | None]:
        """Return (status, algo_versions, analysis_result_id) for latest run.

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

        stored = row.engine_versions_json
        if not stored or "identity" not in stored:
            return AnalysisStatus.STALE_VERSION, None, row.id

        try:
            algo = AlgoVersions.model_validate(stored)
        except Exception:
            return AnalysisStatus.STALE_VERSION, None, row.id

        current = self._current_algorithm_identity()
        if algo.identity.model_dump() != current.model_dump():
            return AnalysisStatus.STALE_VERSION, algo, row.id

        return AnalysisStatus.OK, algo, row.id

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
            session_stmt = select(models.Session).where(
                models.Session.id == session_id,
                models.Session.day_id.in_(
                    select(models.Day.id).where(models.Day.date == query.therapy_date)
                ),
            )
            session_row = (await self._db.execute(session_stmt)).scalars().first()
            if session_row is None:
                raise ValueError(
                    f"session_id {session_id} does not belong to date {query.therapy_date}"
                )
            device_id = session_row.device_id
        else:
            session_id, device_id = await self._resolve_session_for_date(
                query.therapy_date, query.device_id
            )

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
                    ti=b.inspiration_time_s or 0.0,
                    te=b.expiration_time_s or 0.0,
                    ttot=b.total_time_s or 0.0,
                    ie_ratio=b.i_e_ratio or 0.0,
                    duty_cycle=b.duty_cycle or 0.0,
                    peak_insp_flow=b.peak_flow_lpm or 0.0,
                    peak_exp_flow=0.0,  # not stored separately
                    tidal_volume=b.tidal_volume_ml or 0.0,
                    flatness_index=b.flatness_index or 0.0,
                    mid_insp_flattening=b.mid_insp_flattening or 0.0,
                    flow_class=b.flow_class or 1,
                    flow_class_confidence=b.flow_confidence or 0.0,
                    is_recovery_breath=b.is_recovery_breath or False,
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
                page_size=len(bins),
                bins=bins,
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
        from sqlalchemy import select  # noqa: PLC0415

        from snore.database import models  # noqa: PLC0415

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

        # Fetch sessions for the day
        stmt = (
            select(models.Session, models.Day)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(models.Day.date == therapy_date)
        )
        if device_id is not None:
            stmt = stmt.where(models.Session.device_id == device_id)
        day_rows = (await self._db.execute(stmt)).all()

        if not day_rows:
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

        resolved_device_id = device_id or day_rows[0].Session.device_id

        # Build per-session analysis status
        session_ids = [r.Session.id for r in day_rows]
        session_starts = {r.Session.id: r.Session.start_time for r in day_rows}

        coverage: list[SessionCoverage] = []
        identities: list[AlgorithmIdentity] = []
        primary_modes: list[str] = []
        for sid in session_ids:
            status, algo, _ = await self._latest_analysis_for_session(sid)
            coverage.append(
                SessionCoverage(
                    session_id=sid, analysis_status=status, algo_versions=algo
                )
            )
            if status == AnalysisStatus.OK and algo is not None:
                identities.append(algo.identity)
                primary_modes.append(algo.run.primary_mode)

        # Determine day_status
        statuses = {c.analysis_status for c in coverage}
        if statuses == {AnalysisStatus.OK}:
            day_status = DayAnalysisStatus.OK
        elif AnalysisStatus.OK not in statuses:
            day_status = (
                DayAnalysisStatus.NOT_RUN
                if statuses == {AnalysisStatus.NOT_RUN}
                else DayAnalysisStatus.STALE
            )
        else:
            # Mix of OK and stale/not-run
            identity_dicts = [id_.model_dump() for id_ in identities]
            if len({str(d) for d in identity_dicts}) > 1:
                day_status = DayAnalysisStatus.MIXED_VERSION
            else:
                day_status = DayAnalysisStatus.PARTIAL

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

        # Only pass primary_mode when criterion uses recovery markers
        result_primary_mode = (
            uniform_primary_mode
            if criterion == WindowCriterion.FL_RUN_ENDING_IN_RECOVERY
            else None
        )

        # Collect analysis_result_ids per session (for per-window provenance)
        ar_by_session: dict[int, int | None] = {}
        for cov in coverage:
            _, _, ar_id = await self._latest_analysis_for_session(cov.session_id)
            ar_by_session[cov.session_id] = ar_id
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
            if ar_id is None:
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
                if b.leak_valid is True or (
                    opts.include_unknown_leak and b.leak_valid is None
                ):
                    if opts.flattening_threshold is None or (
                        b.mid_insp_flattening is not None
                        and b.mid_insp_flattening >= opts.flattening_threshold
                    ):
                        eligible_indices.append(i)

            # Sort by mid_insp_flattening descending (§6 step 2)
            eligible_indices.sort(
                key=lambda i: breath_rows[i].mid_insp_flattening or 0.0, reverse=True
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
            if ar_id is None:
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

        Refuses on CROSS_VERSION_REFUSAL_KEYS mismatch (ALGO_VERSION_MISMATCH)
        or mid-epoch RX change (RX_CHANGED_WITHIN_EPOCH).  Mixed primary modes
        degrade RERA fields only (PRIMARY_MODE_MISMATCH).
        """
        import statistics  # noqa: PLC0415

        from sqlalchemy import select  # noqa: PLC0415

        from snore.analysis.rx_tracker import RX_KEYS  # noqa: PLC0415
        from snore.database import models  # noqa: PLC0415

        epoch_stats: list[EpochBreathStats] = []
        rx_violations: list[EpochRxViolation] = []

        for epoch in epochs:
            # Fetch all days in range for this epoch's device
            day_stmt = (
                select(models.Day, models.Session)
                .join(models.Session, models.Day.id == models.Session.day_id)
                .where(
                    models.Day.date >= epoch.date_start,
                    models.Day.date <= epoch.date_end,
                )
                .order_by(models.Day.date)
            )
            if epoch.device_id is not None:
                day_stmt = day_stmt.where(models.Session.device_id == epoch.device_id)

            day_rows = (await self._db.execute(day_stmt)).all()

            # Gather contributing sessions (analyzed_session_count > 0)
            # and check RX uniformity
            contributing_sessions: list[
                tuple[int, AlgoVersions]
            ] = []  # (session_id, algo)
            all_rx: list[dict[str, str]] = []
            nights_with_data = 0
            nights_missing_analysis = 0

            # Group by date
            by_date: dict[date, list[int]] = {}
            for row in day_rows:
                d = row.Day.date
                if d not in by_date:
                    by_date[d] = []
                by_date[d].append(row.Session.id)

            for _day_date, sids in by_date.items():
                ok_sessions: list[tuple[int, AlgoVersions]] = []
                for sid in sids:
                    status, algo, _ = await self._latest_analysis_for_session(sid)
                    if status == AnalysisStatus.OK and algo is not None:
                        ok_sessions.append((sid, algo))

                if ok_sessions:
                    nights_with_data += 1
                    contributing_sessions.extend(ok_sessions)

                    # Collect RX settings for this day (from first session)
                    sess_row = (
                        (
                            await self._db.execute(
                                select(models.Setting).where(
                                    models.Setting.session_id == sids[0],
                                    models.Setting.key.in_(RX_KEYS),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    rx = {s.key: s.value for s in sess_row if s.value is not None}
                    all_rx.append(rx)
                else:
                    nights_missing_analysis += 1

            # Check RX uniformity within epoch
            if all_rx:
                first_rx = all_rx[0]
                rx_changed = any(rx != first_rx for rx in all_rx[1:])
                if rx_changed:
                    # Find change dates
                    change_dates = []
                    changed_keys = set()
                    for i in range(1, len(all_rx)):
                        diffs = {
                            k
                            for k in set(all_rx[i - 1]) | set(all_rx[i])
                            if all_rx[i - 1].get(k) != all_rx[i].get(k)
                        }
                        if diffs:
                            changed_keys |= diffs
                            d = list(by_date.keys())[i]
                            change_dates.append(d)
                    rx_violations.append(
                        EpochRxViolation(
                            epoch_label=epoch.label,
                            changed_keys=sorted(changed_keys),
                            change_dates=change_dates,
                        )
                    )

            if not contributing_sessions:
                epoch_stats.append(
                    EpochBreathStats(
                        label=epoch.label,
                        date_start=epoch.date_start,
                        date_end=epoch.date_end,
                        nights_with_data=0,
                        nights_missing_analysis=nights_missing_analysis,
                        algorithm_identity=None,
                        null_reason=NullReason.NO_DATA_IN_RANGE,
                        primary_mode=None,
                        mid_insp_flattening=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        flatness_index=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        flow_class_distribution={},
                        tidal_volume_ml=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        ie_ratio=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        rera_proxy_count=None,
                        rera_reason=NullReason.ANALYSIS_NOT_RUN,
                        rx_settings=first_rx if all_rx else {},
                    )
                )
                continue

            # Check algorithm identity uniformity (CROSS_VERSION_REFUSAL_KEYS)
            all_identities = [algo.identity for _, algo in contributing_sessions]
            first_id = all_identities[0].model_dump()
            cross_keys = CROSS_VERSION_REFUSAL_KEYS
            identity_uniform = all(
                {k: id_.model_dump()[k] for k in cross_keys}
                == {k: first_id[k] for k in cross_keys}
                for id_ in all_identities[1:]
            )
            if not identity_uniform:
                epoch_stats.append(
                    EpochBreathStats(
                        label=epoch.label,
                        date_start=epoch.date_start,
                        date_end=epoch.date_end,
                        nights_with_data=nights_with_data,
                        nights_missing_analysis=nights_missing_analysis,
                        algorithm_identity=None,
                        null_reason=NullReason.ALGO_VERSION_MISMATCH,
                        primary_mode=None,
                        mid_insp_flattening=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        flatness_index=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        flow_class_distribution={},
                        tidal_volume_ml=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        ie_ratio=DistributionStats(
                            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
                        ),
                        rera_proxy_count=None,
                        rera_reason=NullReason.ANALYSIS_NOT_RUN,
                        rx_settings=all_rx[0] if all_rx else {},
                    )
                )
                continue

            # Check primary_mode uniformity for RERA
            all_modes_str = [algo.run.primary_mode for _, algo in contributing_sessions]
            if len(set(all_modes_str)) == 1:
                uniform_primary_mode: str | None = all_modes_str[0]
                rera_reason: NullReason | None = None
            else:
                uniform_primary_mode = None
                rera_reason = NullReason.PRIMARY_MODE_MISMATCH

            # Fetch all leak-valid breaths for contributing sessions
            contributing_ar_ids = []
            for sid, _ in contributing_sessions:
                _, _, ar_id = await self._latest_analysis_for_session(sid)
                if ar_id is not None:
                    contributing_ar_ids.append((sid, ar_id))

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

            # RERA proxy: FL runs ending in recovery breath in each contributing session
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
                    # Count FL-run-ending-in-recovery occurrences
                    i = 0
                    while i < len(brows_all):
                        b = brows_all[i]
                        if b.flow_class is not None and b.flow_class >= 4:
                            j = i
                            while j < len(brows_all) and (
                                brows_all[j].flow_class is not None
                                and (brows_all[j].flow_class or 0) >= 4
                            ):
                                j += 1
                            fl_len = j - i
                            if (
                                j < len(brows_all)
                                and brows_all[j].is_recovery_breath
                                and fl_len >= 2
                            ):
                                rera_count += 1
                            i = j
                        else:
                            i += 1

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
                    mid_insp_flattening=_distrib(mif_vals, n_lv, n_nights_contrib),
                    flatness_index=_distrib(fi_vals, n_lv, n_nights_contrib),
                    flow_class_distribution=fc_dist,
                    tidal_volume_ml=_distrib(tv_vals, n_lv, n_nights_contrib),
                    ie_ratio=_distrib(ie_vals, n_lv, n_nights_contrib),
                    rera_proxy_count=rera_count,
                    rera_reason=rera_reason,
                    rx_settings=all_rx[0] if all_rx else {},
                )
            )

        null_reason: NullReason | None = None
        if rx_violations:
            null_reason = NullReason.RX_CHANGED_WITHIN_EPOCH

        return CompareEpochsResult(
            epochs=epoch_stats,
            null_reason=null_reason,
            rx_violations=rx_violations,
        )

    async def get_analysis_status(
        self,
        session_id: int,
    ) -> tuple[AnalysisStatus, AlgoVersions | None]:
        """(status, versions) for a session's latest AnalysisResult."""
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

    async def get_nightly_summary(
        self,
        therapy_date: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyAnalysisSummary:
        """Latest-run analysis fields aggregated across all OK sessions of a day."""
        raise NotImplementedError("get_nightly_summary — PR-A seam; implementation TBD")

    async def get_nightly_range_summary(
        self,
        date_start: date,
        date_end: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyRangeSummary:
        """Per-night summaries + aggregate compliance."""
        raise NotImplementedError(
            "get_nightly_range_summary — PR-A seam; implementation TBD"
        )

    async def get_device_capabilities(
        self,
        device_id: int,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> DeviceCapabilities:
        """Actual covered range + channels, event types, setting keys present."""
        from snore.parsers.register_all import (
            ensure_registered_parsers,  # noqa: PLC0415
        )

        ensure_registered_parsers()
        raise NotImplementedError(
            "get_device_capabilities — PR-A seam; implementation TBD"
        )

    async def get_contextual_events(
        self,
        therapy_date: date,
        event_types: list[str] | None = None,
        min_duration: float | None = None,
        device_id: int | None = None,
    ) -> list[ContextualEvent]:
        """Machine events enriched with waveform context."""
        raise NotImplementedError(
            "get_contextual_events — PR-A seam; implementation TBD"
        )

    async def get_waveform_window(
        self, request: WaveformWindowRequest
    ) -> WaveformWindow:
        """Convenience orchestrator: fetch then compute. Never closes self._db."""
        raw = await fetch_waveform_window_raw(self._db, request)
        return compute_waveform_window(raw)

    async def get_ca_analysis(
        self, therapy_date: date, device_id: int | None = None
    ) -> CaAnalysisResult:
        """Per-CA context + night-level periodic-breathing stats."""
        raise NotImplementedError("get_ca_analysis — PR-A seam; implementation TBD")

    @staticmethod
    def _current_algorithm_identity() -> AlgorithmIdentity:
        """Current algorithm identity for STALE_VERSION detection."""
        return AlgorithmIdentity.current()
