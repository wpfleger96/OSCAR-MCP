"""BreathService — query layer over the breaths table.

All types in this module are the Appendix-A typed seam definitions (plan v3.8).
PR-B (Duncan) consumes these seams; PR-A (this PR) defines and implements them.

All types live here per Appendix A §13 note ("All types live in
src/snore/services/breath_service.py").
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

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

    async def get_breath_table(self, query: BreathQueryRange) -> BreathPage:
        """Raw or binned breath fetch.

        Latest analysis run per session selected by (created_at DESC, id DESC).
        analysis_status=NOT_RUN when no AnalysisResult exists;
        STALE_VERSION when engine_versions_json differs from current identity.
        """
        raise NotImplementedError(
            "get_breath_table is a PR-A seam defined here; "
            "full implementation ships with this PR"
        )

    async def find_windows(
        self,
        therapy_date: date,
        criterion: WindowCriterion,
        n: int,
        options: WindowCriterionOptions | None = None,
        device_id: int | None = None,
    ) -> FindWindowsResult:
        """N windows matching criterion, worst first."""
        raise NotImplementedError("find_windows — PR-A seam; implementation TBD")

    async def compare_epochs(
        self,
        epochs: list[EpochRequest],
        metrics: list[DistributionMetric] | None = None,
    ) -> CompareEpochsResult:
        """Distributions across RxTracker epochs."""
        raise NotImplementedError("compare_epochs — PR-A seam; implementation TBD")

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
