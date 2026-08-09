"""Pydantic response schemas for SNORE MCP tools.

Timestamp contract (three tiers, A6):
  Tier 1 — absolute audit instants (e.g. ``AnalysisResult.created_at``):
    UTC ISO 8601 with ``Z`` suffix.
  Tier 2 — device/session wall-clock times (e.g. ``Event.start_time``,
    ``Session.start_time``): offset-free ISO 8601 string (the DB deliberately
    stores these as naive datetimes — no TZ is known from the source device).
    Always accompanied by ``timezone_status: "unknown"``.  Never emit a UTC
    offset or fabricate one via ``.timestamp()`` / ``astimezone()``.
  Tier 3 — in-session positions: numeric ``offset_seconds`` from
    ``Session.start_time``.

Absent data is ``null`` with a companion ``*_reason`` field
(e.g. ``rera_index: null, rera_index_reason: "analysis_not_run"``).
All measurement fields carry their unit in the field name or tool docstring.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class DeviceCapabilities(BaseModel):
    """Capabilities declared by the device/dataset for a queried range (G2)."""

    model_config = ConfigDict(populate_by_name=True)

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    has_flow_waveform: bool
    has_pressure_waveform: bool
    has_leak_waveform: bool
    has_spo2: bool
    has_events: bool
    has_analysis: bool
    notes: list[str] = []


class DeviceInfo(BaseModel):
    """Summary of a single device."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    manufacturer: str
    model: str
    serial_number: str
    first_session_date: date | None = None
    last_session_date: date | None = None
    session_count: int = 0
    therapy_modes: list[str] = []
    device_capabilities: DeviceCapabilities | None = None


class DataOverviewResponse(BaseModel):
    """Response from get_data_overview."""

    model_config = ConfigDict(populate_by_name=True)

    devices: list[DeviceInfo]
    date_range_start: date | None = None
    date_range_end: date | None = None
    total_sessions: int = 0
    available_waveform_channels: list[str] = []
    available_event_types: list[str] = []
    analysis_run: bool = False
    analysis_session_count: int = 0


class SettingsEpoch(BaseModel):
    """A contiguous period of stable therapy settings."""

    model_config = ConfigDict(populate_by_name=True)

    start_date: date
    end_date: date
    nights: int
    settings: dict[str, str | None]
    changed_keys: list[str] = []
    device_id: int | None = None


class SettingsTimelineResponse(BaseModel):
    """Response from get_settings_timeline."""

    model_config = ConfigDict(populate_by_name=True)

    epochs: list[SettingsEpoch]
    total_epochs: int
    device_capabilities_by_device: dict[str, DeviceCapabilities] = {}


class NightlyRow(BaseModel):
    """Per-night summary row returned by get_nightly_summary."""

    model_config = ConfigDict(populate_by_name=True)

    date: date
    usage_hours: float | None = None
    session_count: int = 0

    # AHI components (events/hr) — null + reason when absent
    ahi: float | None = None
    oai: float | None = None
    cai: float | None = None
    hi: float | None = None

    # Analysis-derived indices — null when analysis has not been run
    rera_index: float | None = None
    rera_index_reason: str | None = None
    rdi: float | None = None
    rdi_reason: str | None = None

    # Pressure percentiles (cmH₂O)
    pressure_median_cmh2o: float | None = None
    pressure_95th_cmh2o: float | None = None
    epap_median_cmh2o: float | None = None

    # Leak (L/min)
    leak_median_lpm: float | None = None
    leak_95th_lpm: float | None = None
    leak_above_24_pct: float | None = None
    leak_above_24_pct_reason: str | None = None

    # Resp physiology
    rr_mean_bpm: float | None = None
    tv_mean_ml: float | None = None
    mv_mean_lpm: float | None = None

    # SpO₂ (%)
    spo2_mean_pct: float | None = None

    # Breath-level FL/RERA fields (from BreathService.get_nightly_summary)
    fl_median: float | None = None
    fl_median_reason: str | None = None
    fl_p95: float | None = None
    fl_p95_reason: str | None = None
    fl_max: float | None = None
    fl_max_reason: str | None = None
    # Percent of leak-valid classified breaths with flow_class >= 4
    fl_class_ge4_pct: float | None = None
    fl_class_ge4_pct_reason: str | None = None
    rera_proxy_count: int | None = None
    rera_proxy_reason: str | None = None

    # Breath timing aggregates — from breath-level analysis; null + reason when analysis hasn't run
    ti_median_s: float | None = None
    ti_median_reason: str | None = None
    ie_ratio: float | None = None
    ie_ratio_reason: str | None = None

    device_id: int | None = None


class ComplianceFields(BaseModel):
    """Compliance summary appended to range-mode nightly summary."""

    model_config = ConfigDict(populate_by_name=True)

    threshold_hours: float
    days_compliant: int
    days_total: int
    compliance_pct: float


class NightlySummaryResponse(BaseModel):
    """Response from get_nightly_summary."""

    model_config = ConfigDict(populate_by_name=True)

    nights: list[NightlyRow]
    total_nights: int
    page: int
    page_size: int
    # Compliance block only present in range mode
    compliance: ComplianceFields | None = None
    device_capabilities: DeviceCapabilities | None = None


class EventContext(BaseModel):
    """Per-event contextual snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    pressure_at_event_cmh2o: float | None = None
    leak_at_event_lpm: float | None = None
    mv_prior_120s_lpm: float | None = None
    minutes_since_session_start: float | None = None


class EventRow(BaseModel):
    """A single respiratory event with inline context.

    Timestamp contract (A6):
    - ``start_time_wall_clock``: device wall-clock, offset-free ISO 8601 (tier 2).
    - ``session_start_wall_clock``: per-event session anchor, offset-free ISO 8601 (tier 2).
    - ``timezone_status``: always ``"unknown"`` — no TZ is recorded for device times.
    - ``offset_seconds``: position from this event's session start (tier 3).
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: int  # session that produced this event (per-event anchor)
    session_start_wall_clock: (
        str  # offset-free ISO 8601 device wall-clock for this event's session
    )
    event_type: str
    start_time_wall_clock: str  # offset-free ISO 8601 device wall-clock (tier 2)
    timezone_status: str = "unknown"  # always "unknown" for device wall-clock
    offset_seconds: float  # seconds from this event's Session.start_time (tier 3)
    duration_seconds: float | None = None
    spo2_drop_pct: float | None = None
    peak_flow_limitation: float | None = None
    pressure_reason: str | None = None
    leak_reason: str | None = None
    mv_reason: str | None = None
    context: EventContext | None = None


class EventsResponse(BaseModel):
    """Response from get_events.

    ``session_id`` and ``session_start_wall_clock`` are the response-level anchors.
    They are null when no events were returned or when events span multiple sessions.
    Per-event anchors (``EventRow.session_id`` and ``EventRow.session_start_wall_clock``)
    are always populated on individual events.
    """

    model_config = ConfigDict(populate_by_name=True)

    date: str
    session_id: int | None = None  # null when empty or multi-session
    session_start_wall_clock: str | None = None  # null when empty or multi-session
    timezone_status: str = "unknown"
    events: list[EventRow]
    total_events: int
    truncated: bool = False
    device_capabilities: DeviceCapabilities | None = None


class CapabilityEntry(BaseModel):
    """One entry in the capabilities resource."""

    model_config = ConfigDict(populate_by_name=True)

    channel: str
    description: str
    unit: str | None = None
    present_in_dataset: bool
    sample_rate_hz: float | None = None


# ---------------------------------------------------------------------------
# Stage-2 schemas: get_breath_table, find_windows, compare_epochs
# ---------------------------------------------------------------------------


class BreathTableQuery(BaseModel):
    """Echo of the breath-table query as resolved by the service."""

    model_config = ConfigDict(populate_by_name=True)

    therapy_date: str
    device_id: int | None = None
    session_id: int | None = None
    offset_start: float
    offset_end: float
    page: int
    page_size: int
    bin_minutes: float | None = None


class BreathTableRow(BaseModel):
    """One analyzed breath (tier-2 wall-clock anchor + tier-3 offsets).

    Nullable measurement fields carry companion ``*_reason`` fields where the
    service provides them; absence of a value is never coerced to zero.
    """

    model_config = ConfigDict(populate_by_name=True)

    analysis_result_id: int
    session_id: int
    breath_number: int
    session_start_wall_clock: str
    timezone_status: str = "unknown"
    start_offset_seconds: float
    end_offset_seconds: float
    ti_s: float | None = None
    te_s: float | None = None
    ttot_s: float | None = None
    ie_ratio: float | None = None
    duty_cycle: float | None = None
    peak_insp_flow_lpm: float | None = None
    peak_exp_flow_lpm: float | None = None
    tidal_volume_ml: float | None = None
    flatness_index: float | None = None
    mid_insp_flattening: float | None = None
    flow_class: int | None = None
    flow_class_confidence: float | None = None
    is_recovery_breath: bool | None = None
    trigger_type: str | None = None
    cycle_type: str | None = None
    trigger_cycle_confidence: float | None = None
    trigger_cycle_experimental: bool = True
    trigger_cycle_applicability: str | None = None
    trigger_cycle_reason: str | None = None
    leak_valid: bool | None = None
    leak_valid_reason: str | None = None
    ramp_active: bool | None = None
    ramp_active_reason: str | None = None
    mask_off: bool | None = None
    mask_off_reason: str | None = None


class BreathTableBin(BaseModel):
    """Aggregated breath metrics for one time bin."""

    model_config = ConfigDict(populate_by_name=True)

    session_start_wall_clock: str
    timezone_status: str = "unknown"
    bin_start_offset: float
    bin_end_offset: float
    breath_count: int
    flatness_index_median: float | None = None
    mid_insp_flattening_median: float | None = None
    flow_class_mode: int | None = None
    tidal_volume_median_ml: float | None = None
    ie_ratio_median: float | None = None
    leak_valid_fraction: float | None = None
    analysis_status: str


class BreathTableResponse(BaseModel):
    """Response from get_breath_table. Exactly one of rows/bins is populated."""

    model_config = ConfigDict(populate_by_name=True)

    query: BreathTableQuery
    session_id: int | None = None
    session_start_wall_clock: str | None = None
    timezone_status: str = "unknown"
    analysis_status: str
    algo_versions: dict[str, Any] | None = None
    null_reason: str | None = None
    is_binned: bool
    total_breaths: int
    page: int
    page_size: int
    rows: list[BreathTableRow] = []
    bins: list[BreathTableBin] = []
    device_capabilities: DeviceCapabilities | None = None


class SessionCoverageEntry(BaseModel):
    """Per-session analysis coverage for a queried day."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: int
    analysis_status: str
    algo_versions: dict[str, Any] | None = None


class WindowRow(BaseModel):
    """One found window, worst-first ordering within the response."""

    model_config = ConfigDict(populate_by_name=True)

    criterion: str
    session_id: int
    session_start_wall_clock: str
    timezone_status: str = "unknown"
    window_start_offset: float
    window_end_offset: float
    reason_summary: str
    worst_mid_insp_flattening: float | None = None
    fl_run_length: int | None = None
    anchor_event_offset: float | None = None
    analysis_result_id: int | None = None
    analysis_status: str
    analysis_reason: str | None = None


class FindWindowsResponse(BaseModel):
    """Response from find_windows."""

    model_config = ConfigDict(populate_by_name=True)

    query_date: str
    device_id: int | None = None
    criterion: str
    day_status: str
    session_coverage: list[SessionCoverageEntry] = []
    algorithm_identity: dict[str, Any] | None = None
    null_reason: str | None = None
    primary_mode: str | None = None
    windows: list[WindowRow] = []
    device_capabilities: DeviceCapabilities | None = None


class EpochSpec(BaseModel):
    """Input epoch for compare_epochs (dates are YYYY-MM-DD strings)."""

    model_config = ConfigDict(populate_by_name=True)

    label: str
    date_start: str
    date_end: str
    device_id: int | None = None


class EpochDistribution(BaseModel):
    """Descriptive stats for one metric over one epoch (leak-valid breaths only)."""

    model_config = ConfigDict(populate_by_name=True)

    median: float | None = None
    iqr: float | None = None
    p95: float | None = None
    n_breaths: int
    n_nights: int


class EpochRxViolationRow(BaseModel):
    """A therapy-settings change detected inside one epoch's date range."""

    model_config = ConfigDict(populate_by_name=True)

    epoch_label: str
    changed_keys: list[str] = []
    change_dates: list[str] = []


class EpochStats(BaseModel):
    """Breath-feature distributions for one epoch."""

    model_config = ConfigDict(populate_by_name=True)

    label: str
    date_start: str
    date_end: str
    nights_with_data: int
    nights_missing_analysis: int
    algorithm_identity: dict[str, Any] | None = None
    null_reason: str | None = None
    primary_mode: str | None = None
    mid_insp_flattening: EpochDistribution
    flatness_index: EpochDistribution
    flow_class_distribution: dict[str, int] = {}
    tidal_volume_ml: EpochDistribution
    ie_ratio: EpochDistribution
    rera_proxy_count: int | None = None
    rera_reason: str | None = None
    rx_settings: dict[str, str] = {}


class CompareEpochsResponse(BaseModel):
    """Response from compare_epochs."""

    model_config = ConfigDict(populate_by_name=True)

    epochs: list[EpochStats] = []
    null_reason: str | None = None
    rx_violations: list[EpochRxViolationRow] = []


# ---------------------------------------------------------------------------
# Stage-3 schemas: get_waveform, get_ca_analysis
# ---------------------------------------------------------------------------


class WaveformChannelSchema(BaseModel):
    """One deserialized, windowed waveform channel returned by get_waveform."""

    model_config = ConfigDict(populate_by_name=True)

    channel_type: str
    unit: str | None = None
    sample_rate_hz: float
    offset_seconds: list[float]  # tier-3 positions from session start
    values: list[float]
    original_sample_count: int  # pre-LTTB count within the window
    is_downsampled: bool


class WaveformWindowResponse(BaseModel):
    """Response from get_waveform."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: int | None = None  # null when no session on the date
    session_start_wall_clock: str | None = (
        None  # tier-2 naive ISO; null when session_id null
    )
    timezone_status: str = "unknown"
    window_start_offset_s: float
    window_end_offset_s: float
    channels: list[WaveformChannelSchema]
    missing_channels: list[str]
    missing_channel_reason: str | None = None


class CaDetailSchema(BaseModel):
    """One central apnea event with context."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: int
    session_start_wall_clock: str  # tier-2 naive ISO
    timezone_status: str = "unknown"
    offset_seconds: float  # tier-3 CA start from session start
    duration_seconds: float | None = None
    preceding_mv_slope_lpm_per_min: float | None = None
    preceding_mv_slope_reason: str | None = None
    ps_delivered_cmh2o: float | None = None
    ps_reason: str | None = None
    stability_index: float | None = None
    stability_reason: str | None = None
    # MV provenance: "device" | "flow_derived" | null (no MV channel available)
    mv_source: str | None = None


class CaAnalysisResponse(BaseModel):
    """Response from get_ca_analysis."""

    model_config = ConfigDict(populate_by_name=True)

    query_date: str
    device_id: int
    day_status: str
    # SessionCoverageEntry reused from Stage-2 (session_id, analysis_status, algo_versions)
    session_coverage: list[SessionCoverageEntry] = []
    algorithm_identity: dict[str, Any] | None = None
    null_reason: str | None = None
    ca_events: list[CaDetailSchema] = []
    periodic_breathing_pct: float | None = None
    pb_reason: str | None = None
    mv_rolling_variance: float | None = None
    mv_variance_reason: str | None = None
    # Night-level MV provenance: "device" | "flow_derived" | "mixed" | null
    mv_source: str | None = None
    mv_fallback_version: str | None = None
    device_capabilities: DeviceCapabilities | None = None


# Mapping used for docs://schemas/{type} — maps schema name to model class
SCHEMA_MODEL_MAP: dict[str, type[BaseModel]] = {
    "device_capabilities": DeviceCapabilities,
    "device_info": DeviceInfo,
    "data_overview": DataOverviewResponse,
    "settings_epoch": SettingsEpoch,
    "settings_timeline": SettingsTimelineResponse,
    "nightly_row": NightlyRow,
    "compliance_fields": ComplianceFields,
    "nightly_summary": NightlySummaryResponse,
    "event_context": EventContext,
    "event_row": EventRow,
    "events_response": EventsResponse,
    "capability_entry": CapabilityEntry,
    # Stage 2
    "breath_table_query": BreathTableQuery,
    "breath_table_row": BreathTableRow,
    "breath_table_bin": BreathTableBin,
    "breath_table_response": BreathTableResponse,
    "window_row": WindowRow,
    "session_coverage_entry": SessionCoverageEntry,
    "find_windows_response": FindWindowsResponse,
    "epoch_spec": EpochSpec,
    "epoch_distribution": EpochDistribution,
    "epoch_stats": EpochStats,
    "epoch_rx_violation": EpochRxViolationRow,
    "compare_epochs_response": CompareEpochsResponse,
    # Stage 3
    "waveform_channel": WaveformChannelSchema,
    "waveform_window": WaveformWindowResponse,
    "ca_detail": CaDetailSchema,
    "ca_analysis": CaAnalysisResponse,
}


def model_to_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the JSON schema for a Pydantic model."""
    return model.model_json_schema()
