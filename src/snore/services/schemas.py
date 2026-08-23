"""Pydantic response schemas for service layer.

These models define the contract between services and consumers (CLI/API).
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PeriodStatistics",
    "EventValidationResult",
    "DatabaseStats",
    "SessionListItem",
    "SessionListResult",
    "SessionDetail",
    "SessionStatistics",
    "SessionSetting",
    "DeletePreview",
    "TherapySummary",
    "EventTypeCount",
    "WaveformInfo",
    "AnalysisListItem",
    "AnalysisSessionDetail",
    "AnalysisDeletePreview",
    "EventMatchResult",
    "DeviceInfo",
    "SettingChangeEntry",
    "SettingsChange",
    "DeviceUsageSummary",
    "DeviceDetail",
    # Mask equipment log schemas (consumed by MaskLogService and API routers)
    "MaskLogEntryResponse",
    "MaskEpochResponse",
    # RX / Day / Event schemas (consumed by RxService, DayService, and API routers)
    "DayListItem",
    "DayDetail",
    "RxPeriodResponse",
    "RxComparisonResponse",
    "RxSettingChange",
    "RxChangesResponse",
    "RxAllResponse",
    "MergedSettingsChange",
    # Import schemas
    "ImportSource",
    "ImportSourceResult",
    "ImportResult",
    # Batch analysis schemas
    "BatchSessionResult",
    "BatchAnalysisResult",
    # Event comparison schemas
    "EventComparisonDetail",
    "EventComparisonResult",
    # Vacuum schema
    "VacuumResult",
    # Reset schema
    "ResetResult",
    # Per-user data deletion schema
    "DeleteDataResult",
    # Stats range schema
    "DataRange",
    # Apple Health import schema
    "HealthImportResult",
    # Apple Health read schemas
    "HealthNightSummaryRead",
    "HealthNightDetailRead",
    "HealthSampleRead",
]


class PeriodStatistics(BaseModel):
    """Statistics for a time period (week, month, year)."""

    period_type: str = Field(description="Type: daily, weekly, monthly, yearly")
    period_start: date
    period_end: date

    days_used: int = Field(default=0, description="Number of days with therapy")
    days_in_period: int = Field(default=0, description="Total days in period")
    avg_hours_per_day: float | None = Field(
        default=None, description="Average hours per day used"
    )

    avg_ahi: float | None = Field(default=None, description="Average AHI")
    median_ahi: float | None = Field(default=None, description="Median AHI")
    avg_pressure: float | None = Field(
        default=None, description="Average pressure (cmH₂O)"
    )
    avg_leak: float | None = Field(
        default=None, description="Average leak rate (L/min)"
    )

    avg_spo2: float | None = Field(default=None, description="Average SpO₂ (%)")
    min_spo2: float | None = Field(default=None, description="Minimum SpO₂ (%)")

    avg_total_sleep_hours: float | None = Field(
        default=None, description="Average total sleep hours per night (Apple Health)"
    )
    avg_sleep_efficiency_pct: float | None = Field(
        default=None, description="Average sleep efficiency % per night (Apple Health)"
    )

    avg_oai: float | None = Field(default=None, description="Average OAI (events/hour)")
    avg_cai: float | None = Field(default=None, description="Average CAI (events/hour)")
    avg_hi: float | None = Field(default=None, description="Average HI (events/hour)")
    avg_rera: float | None = Field(
        default=None, description="Average RERA index (events/hour)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "period_type": "monthly",
                "period_start": "2024-01-01",
                "period_end": "2024-01-31",
                "days_used": 29,
                "days_in_period": 31,
                "avg_hours_per_day": 7.2,
                "avg_ahi": 2.8,
                "median_ahi": 2.3,
                "avg_pressure": 10.5,
                "avg_leak": 9.2,
                "avg_spo2": 96.2,
                "min_spo2": 89,
            }
        }
    )


class EventValidationResult(BaseModel):
    """
    Validation results comparing programmatic vs machine-detected events.

    Useful for tuning detection thresholds and assessing algorithm accuracy.
    """

    machine_event_count: int = Field(description="Events detected by CPAP machine")
    programmatic_event_count: int = Field(
        description="Events detected programmatically"
    )
    matched_events: int = Field(
        description="Events matched between machine and programmatic (within 5s)"
    )
    false_positives: int = Field(
        description="Programmatic events not matched to machine events"
    )
    false_negatives: int = Field(
        description="Machine events not matched to programmatic events"
    )
    sensitivity: float = Field(
        ge=0, le=1, description="Recall: matched / (matched + false_negatives)"
    )
    precision: float = Field(
        ge=0, le=1, description="Precision: matched / (matched + false_positives)"
    )
    f1_score: float = Field(
        ge=0,
        le=1,
        description="F1 score: 2 * (precision * sensitivity) / (precision + sensitivity)",
    )
    agreement_percentage: float = Field(
        ge=0,
        le=100,
        description="Overall agreement: matched / max(machine, programmatic) * 100",
    )


class DatabaseStats(BaseModel):
    """Database statistics including table row counts and coverage metrics."""

    db_path: str = Field(description="Path to the database file")
    size_mb: float = Field(description="Database file size in megabytes")
    profile_count: int = Field(description="Number of profiles")
    device_count: int = Field(description="Number of devices")
    session_count: int = Field(description="Number of sessions")
    day_count: int = Field(description="Number of days")
    event_count: int = Field(description="Number of events")
    waveform_count: int = Field(description="Number of waveform records")
    analysis_count: int = Field(description="Number of analysis results")
    pattern_count: int = Field(description="Number of detected patterns")
    sessions_with_waveforms: int = Field(description="Sessions that have waveform data")
    sessions_with_events: int = Field(description="Sessions that have event data")
    sessions_with_analysis: int = Field(
        description="Distinct sessions having at least one analysis result"
    )
    analyzable_session_count: int = Field(
        description="Sessions having a flow waveform (prerequisite for analysis)"
    )
    waveform_coverage_pct: float = Field(
        description="Percentage of sessions with waveforms"
    )
    event_coverage_pct: float = Field(description="Percentage of sessions with events")
    analysis_coverage_pct: float = Field(
        description=(
            "Percentage of analyzable sessions (those with a flow waveform) that have "
            "been analyzed: sessions_with_analysis / analyzable_session_count * 100"
        )
    )
    first_session: datetime | None = Field(
        default=None, description="Earliest session date"
    )
    last_session: datetime | None = Field(
        default=None, description="Latest session date"
    )


class SessionListItem(BaseModel):
    """Single session item in a list view."""

    id: int = Field(description="Session database ID")
    therapy_day: date = Field(
        description="Therapy day (noon-cutoff date): sessions before 12:00 belong to the previous calendar day"
    )
    start_time: datetime = Field(description="Session start timestamp")
    duration_hours: float = Field(description="Session duration in hours")
    enabled: bool = Field(description="Whether session is enabled for stats")
    manufacturer: str = Field(description="Device manufacturer")
    model: str = Field(description="Device model")
    serial_number: str = Field(description="Device serial number")
    ahi: float | None = Field(default=None, description="Apnea-Hypopnea Index")


class SessionListResult(BaseModel):
    """Result of a session list query with pagination info."""

    sessions: list[SessionListItem] = Field(description="List of session items")
    total_count: int = Field(description="Total sessions matching filters")
    limit: int = Field(description="Result limit applied")


class SessionStatistics(BaseModel):
    """Statistics for a single session (from Statistics table)."""

    model_config = ConfigDict(from_attributes=True)

    usage_hours: float | None = None
    ahi: float | None = None
    rei: float | None = None
    ahi_device: float | None = None
    oai_device: float | None = None
    cai_device: float | None = None
    hi_device: float | None = None
    oai: float | None = None
    cai: float | None = None
    hi: float | None = None
    obstructive_apneas: int | None = None
    central_apneas: int | None = None
    mixed_apneas: int | None = None
    hypopneas: int | None = None
    reras: int | None = None
    flow_limitations: int | None = None
    pressure_mean: float | None = None
    pressure_min: float | None = None
    pressure_max: float | None = None
    pressure_median: float | None = None
    pressure_95th: float | None = None
    epap_mean: float | None = None
    epap_min: float | None = None
    epap_max: float | None = None
    epap_median: float | None = None
    epap_95th: float | None = None
    ipap_median: float | None = None
    ipap_95th: float | None = None
    ipap_max: float | None = None
    leak_mean: float | None = None
    leak_min: float | None = None
    leak_max: float | None = None
    leak_median: float | None = None
    leak_percentile_70: float | None = None
    leak_95th: float | None = None
    spo2_mean: float | None = None
    spo2_min: float | None = None
    spo2_max: float | None = None
    spo2_median: float | None = None
    spo2_95th: float | None = None
    spo2_time_below_90: int | None = None
    pulse_mean: float | None = None
    pulse_min: float | None = None
    pulse_max: float | None = None
    respiratory_rate_mean: float | None = None
    respiratory_rate_min: float | None = None
    respiratory_rate_max: float | None = None
    respiratory_rate_95th: float | None = None
    tidal_volume_mean: float | None = None
    tidal_volume_min: float | None = None
    tidal_volume_max: float | None = None
    tidal_volume_95th: float | None = None
    minute_ventilation_mean: float | None = None
    minute_ventilation_min: float | None = None
    minute_ventilation_max: float | None = None
    minute_ventilation_95th: float | None = None
    uai: float | None = None
    ai: float | None = None
    rin: float | None = None
    csr_pct: float | None = None
    spont_cyc_pct: float | None = None
    ie_ratio_median: float | None = None
    ie_ratio_95th: float | None = None
    ie_ratio_max: float | None = None
    ti_median: float | None = None
    ti_95th: float | None = None
    ti_max: float | None = None
    flow_5th: float | None = None
    flow_95th: float | None = None
    blow_press_5th: float | None = None
    blow_press_95th: float | None = None
    blow_flow_median: float | None = None
    amb_humidity_median: float | None = None
    hum_temp_median: float | None = None
    htube_temp_median: float | None = None
    htube_pow_median: float | None = None
    hum_pow_median: float | None = None
    mask_events: float | None = None


class MaskLogEntryResponse(BaseModel):
    """A single user-entered mask equipment log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    brand: str | None = None
    model: str | None = None
    size: str | None = None
    style: str | None = None
    start_date: date | None = None
    notes: str | None = None


class MaskEpochResponse(BaseModel):
    """A contiguous run of nights sharing one device-reported mask type.

    style is the normalized mask_log-style value (None when the device value is
    unrecognized).  device_id identifies the reporting device for multi-device
    installs.
    """

    mask_type: str
    style: str | None
    start_date: date
    end_date: date
    days_count: int
    device_id: int | None
    device_name: str | None


class SessionSetting(BaseModel):
    """Single setting key-value pair for a session."""

    key: str = Field(description="Setting key")
    value: str | None = Field(default=None, description="Setting value")


class SessionDetail(BaseModel):
    """Detailed view of a single session with all metadata."""

    id: int
    device_session_id: str
    device_manufacturer: str | None
    device_model: str | None
    device_serial: str | None
    therapy_day: date = Field(
        description="Therapy day (noon-cutoff date): sessions before 12:00 belong to the previous calendar day"
    )
    start_time: datetime
    end_time: datetime
    duration_hours: float
    duration_seconds: float
    therapy_mode: str | None
    enabled: bool
    event_count: int
    waveform_count: int
    waveform_types: list[str]
    has_statistics: bool
    has_event_data: bool
    import_source: str | None = None
    parser_version: str | None = None
    data_quality_notes: list[str] = Field(default_factory=list)
    statistics: SessionStatistics | None = None
    settings: list[SessionSetting] | None = None
    active_mask: MaskLogEntryResponse | None = None


class DeletePreview(BaseModel):
    """Preview of sessions and related data to be deleted."""

    sessions: list[SessionListItem] = Field(description="Sessions to be deleted")
    event_count: int = Field(description="Total events to be deleted")
    waveform_count: int = Field(description="Total waveform records to be deleted")
    stats_count: int = Field(description="Total statistics records to be deleted")


class EventTypeCount(BaseModel):
    """Event type with count and percentage."""

    event_type: str
    count: int
    percentage: float


class TherapySummary(BaseModel):
    """Aggregated therapy statistics summary."""

    first_date: date
    last_date: date
    days_since_last: int
    total_hours: float
    avg_hours: float
    days_with_data: int
    avg_ahi: float | None = None
    effectiveness: str = "unknown"
    avg_rei: float | None = None
    avg_pressure: float | None = None
    min_pressure: float | None = None
    max_pressure: float | None = None
    avg_epap: float | None = None
    avg_leak: float | None = None
    avg_spo2: float | None = None
    min_spo2: float | None = None
    total_spo2_time_below_90: int = 0
    avg_pulse: float | None = None
    avg_respiratory_rate: float | None = None
    avg_tidal_volume: float | None = None
    avg_minute_ventilation: float | None = None
    ahi_trend_direction: str | None = None
    event_counts: list[EventTypeCount] = Field(default_factory=list)


class WaveformInfo(BaseModel):
    """Waveform metadata for listing."""

    waveform_type: str
    sample_rate: float
    sample_count: int
    unit: str | None = None
    duration_hours: float


class EventMatchResult(BaseModel):
    """Result of matching machine vs programmatic events."""

    machine_count: int
    programmatic_count: int
    matched: int
    false_positives: int
    false_negatives: int


class AnalysisListItem(BaseModel):
    """Session with analysis status for listing."""

    session_id: int
    session_date: date
    duration_hours: float | None = None
    has_analysis: bool
    analysis_id: int | None = None


class AnalysisSessionDetail(BaseModel):
    """Session detail for analysis deletion preview."""

    id: int
    start_time: datetime
    manufacturer: str | None = None
    model: str | None = None
    version_count: int


class AnalysisDeletePreview(BaseModel):
    """Preview of analysis data to be deleted."""

    sessions_with_analysis: int
    total_analysis_records: int
    records_to_delete: int
    patterns_count: int
    session_details: list[AnalysisSessionDetail] = Field(default_factory=list)


class DeviceInfo(BaseModel):
    """Device information for listing."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str | None = None
    hardware_version: str | None = None
    product_code: str | None = None
    first_seen: datetime
    last_import: datetime | None = None


class SettingChangeEntry(BaseModel):
    """A single setting that changed between two consecutive sessions."""

    key: str
    old_value: str | None
    new_value: str | None


class SettingsChange(BaseModel):
    """Settings that changed for a particular session relative to the prior one."""

    session_id: int
    date: date
    changes: list[SettingChangeEntry]


class DeviceUsageSummary(BaseModel):
    """Aggregated usage statistics for a device."""

    session_count: int
    first_session_date: date | None
    last_session_date: date | None
    total_therapy_hours: float
    therapy_modes: list[str]


class DeviceDetail(DeviceInfo):
    """Full device detail including usage summary, current settings, and settings history."""

    usage: DeviceUsageSummary
    current_settings: dict[str, str] | None
    settings_history: list[SettingsChange]


class HealthNightSummaryRead(BaseModel):
    """Derived nightly sleep summary from Apple Health data."""

    model_config = ConfigDict(from_attributes=True)

    night_date: date
    preferred_source: str | None = None
    time_in_bed_seconds: float | None = None
    total_sleep_seconds: float | None = None
    core_seconds: float | None = None
    deep_seconds: float | None = None
    rem_seconds: float | None = None
    awake_seconds: float | None = None
    unspecified_seconds: float | None = None
    sleep_efficiency_pct: float | None = None
    stage_coverage_pct: float | None = None
    computed_at: datetime


class HealthNightDetailRead(HealthNightSummaryRead):
    """Nightly sleep summary with aggregated oximetry and respiratory rate metrics."""

    avg_spo2_pct: float | None = None
    min_spo2_pct: float | None = None
    avg_rr: float | None = None


class HealthSampleRead(BaseModel):
    """Single Apple Health sample (sleep stage or quantity record)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    record_type: str
    source_name: str
    start_time: datetime
    end_time: datetime
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    night_date: date


class DayListItem(BaseModel):
    """Summary of a single therapy day."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    device_id: int
    session_count: int
    total_therapy_hours: float | None = None
    ahi: float | None = None


class DayDetail(DayListItem):
    """Full detail of a therapy day including per-metric stats.

    The FL/RERA proxy fields below carry ``*_reason`` companions (unlike the
    older nullable stats) because null here is ambiguous — "analysis not run"
    versus a genuine zero — so a companion code disambiguates, mirroring the
    MCP nightly-summary null-with-reason convention.
    """

    oai: float | None = None
    cai: float | None = None
    hi: float | None = None
    avg_pressure: float | None = None
    avg_leak: float | None = None
    avg_spo2: float | None = None
    # Pressure detail
    pressure_min: float | None = None
    pressure_max: float | None = None
    pressure_median: float | None = None
    pressure_95th: float | None = None
    # EPAP detail
    epap_min: float | None = None
    epap_max: float | None = None
    epap_median: float | None = None
    epap_mean: float | None = None
    epap_95th: float | None = None
    # Leak detail
    leak_min: float | None = None
    leak_max: float | None = None
    leak_mean: float | None = None
    leak_95th: float | None = None
    # SpO2 detail
    spo2_min: float | None = None
    spo2_max: float | None = None
    # Raw event counts
    obstructive_apneas: int = 0
    central_apneas: int = 0
    hypopneas: int = 0
    reras: int = Field(
        default=0,
        description="Device-reported RERA count for the night (from the machine).",
    )
    # Nightly breath-analysis proxy metrics, sourced read-time from
    # BreathService (same path as the MCP nightly summary).  Reason semantics:
    # an ordinary un-analyzed night (sessions present, none with an OK analysis)
    # is null with "not_available"; a lookup failure (no sessions for the
    # device, breath-table DB error, device resolution declining) is null with
    # "analysis_not_run".  Day detail never fails on missing breath analysis.
    fl_class_ge4_pct: float | None = Field(
        default=None,
        description=(
            "Percent of rule-classified breaths flagged flow-class >= 4 "
            "(experimental SNORE flow-limitation proxy)."
        ),
    )
    fl_class_ge4_pct_reason: str | None = None
    rera_index: float | None = Field(
        default=None,
        description=(
            "Experimental SNORE RERA-proxy events per therapy hour "
            "(FL-run proxy, not device-reported)."
        ),
    )
    rera_index_reason: str | None = None
    rera_count: int | None = Field(
        default=None,
        description=(
            "Experimental SNORE RERA-proxy count from flow-limitation runs "
            "ending in a recovery breath — distinct from device-reported `reras`."
        ),
    )
    rera_count_reason: str | None = None
    session_ids: list[int] = Field(default_factory=list)
    health_sleep: HealthNightSummaryRead | None = None


class RxPeriodResponse(BaseModel):
    """Single therapy prescription period with aggregated stats."""

    settings: dict[str, str]
    start_date: date
    end_date: date
    days_count: int
    avg_ahi: float | None = None
    median_ahi: float | None = None
    avg_hours: float | None = None
    total_hours: float = 0.0
    avg_leak: float | None = None
    device_id: int | None = None
    device_name: str | None = None


class RxComparisonResponse(BaseModel):
    """RX period comparison result with best/worst indices."""

    periods: list[RxPeriodResponse]
    best_index: int | None = None
    worst_index: int | None = None


class RxSettingChange(BaseModel):
    """A single per-key settings change on a given day for a device."""

    date: date
    device_id: int
    device_name: str
    key: str
    old_value: str | None = None
    new_value: str | None = None


class RxChangesResponse(BaseModel):
    """All settings changes across all devices, sorted by (date, device_id, key)."""

    changes: list[RxSettingChange]


class MergedSettingsChange(BaseModel):
    """One settings change from either the device settings log or the mask log."""

    date: date
    source: str  # "device_settings" | "mask_log"
    device_id: int | None = None  # null for mask_log entries
    device_name: str | None = None
    key: str  # settings key, or "mask_equipment"
    old_value: str | None = None
    new_value: str | None = None
    mask_brand: str | None = None  # mask_log-only detail, null for device_settings
    mask_model: str | None = None
    mask_size: str | None = None
    mask_style: str | None = None
    notes: str | None = None


class RxAllResponse(BaseModel):
    """Combined RX data derived from a single database query."""

    history: list[RxPeriodResponse]
    current: RxPeriodResponse | None = None
    best_index: int | None = None
    worst_index: int | None = None
    changes: RxChangesResponse


class ImportSource(BaseModel):
    """Detected data source for import."""

    parser_name: str = Field(description="Parser identifier (e.g., 'resmed')")
    device_serial: str | None = Field(default=None, description="Device serial number")
    profile_name: str | None = Field(default=None, description="Data profile name")
    structure_type: str | None = Field(
        default=None, description="Directory structure type"
    )
    root_path: str = Field(description="Root path of data source")
    data_root: str | None = Field(default=None, description="Data root within source")


class ImportSourceResult(BaseModel):
    """Result of importing a single data source."""

    source: ImportSource = Field(description="The source that was imported")
    imported: int = Field(default=0, description="Sessions successfully imported")
    skipped: int = Field(default=0, description="Sessions skipped (already exist)")
    failed: int = Field(default=0, description="Sessions that failed to import")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings")
    imported_session_ids: list[int] = Field(
        default_factory=list,
        description="DB Session.id values for sessions that were successfully imported",
    )


class ImportResult(BaseModel):
    """Aggregate result of an import operation across all sources."""

    total_imported: int = Field(default=0, description="Total sessions imported")
    total_skipped: int = Field(default=0, description="Total sessions skipped")
    total_failed: int = Field(default=0, description="Total sessions that failed")
    sources: list[ImportSourceResult] = Field(
        default_factory=list, description="Per-source results"
    )
    warnings: list[str] = Field(default_factory=list, description="Global warnings")
    imported_session_ids: list[int] = Field(
        default_factory=list,
        description="All successfully imported Session.id values across all sources",
    )


class BatchSessionResult(BaseModel):
    """Result of analyzing a single session in a batch."""

    session_id: int = Field(description="Session database ID")
    session_date: date | None = Field(default=None, description="Session date")
    success: bool = Field(description="Whether analysis succeeded")
    cancelled: bool = Field(
        default=False,
        description="Whether this session was skipped due to cancellation",
    )
    error: str | None = Field(default=None, description="Error message if failed")


class BatchAnalysisResult(BaseModel):
    """Aggregate result of batch analysis across multiple sessions."""

    total: int = Field(description="Total sessions processed")
    successful: int = Field(default=0, description="Sessions analyzed successfully")
    failed: int = Field(default=0, description="Sessions that failed analysis")
    cancelled: int = Field(
        default=0, description="Sessions skipped due to cancellation"
    )
    results: list[BatchSessionResult] = Field(
        default_factory=list, description="Per-session results"
    )


class EventComparisonDetail(BaseModel):
    """Detail of a single unmatched event in a comparison."""

    event_type: str = Field(description="Event type (OA, CA, MA, H, etc.)")
    start_time: float = Field(
        description="Event start time in seconds from session start"
    )
    duration: float = Field(description="Event duration in seconds")
    confidence: float | None = Field(
        default=None, description="Detection confidence (programmatic events only)"
    )
    flow_reduction: float | None = Field(
        default=None, description="Flow reduction fraction (programmatic events only)"
    )


class EventComparisonResult(BaseModel):
    """Result of comparing machine vs programmatic events for a session."""

    session_id: int = Field(description="Session database ID")
    mode: str = Field(description="Detection mode used (e.g., 'aasm')")
    machine_event_count: int = Field(description="Total machine-detected events")
    programmatic_event_count: int = Field(
        description="Total programmatically-detected events"
    )
    false_negatives: list[EventComparisonDetail] = Field(
        default_factory=list,
        description="Machine events missed by programmatic detection",
    )
    false_positives_apnea: list[EventComparisonDetail] = Field(
        default_factory=list, description="Programmatic apneas not in machine events"
    )
    false_positives_hypopnea: list[EventComparisonDetail] = Field(
        default_factory=list, description="Programmatic hypopneas not in machine events"
    )


class VacuumResult(BaseModel):
    """Result of a database vacuum operation."""

    status: str = Field(description="Operation status ('success')")
    size_before_mb: float = Field(description="Database size before vacuum in MB")
    size_after_mb: float = Field(description="Database size after vacuum in MB")


class ResetResult(BaseModel):
    """Result of a database reset (delete all data, preserve schema) operation."""

    status: str = Field(description="Operation status ('success')")
    tables_cleared: dict[str, int] = Field(
        description="Rows deleted per table (table_name -> count)"
    )
    total_rows_deleted: int = Field(description="Total rows deleted across all tables")
    size_before_mb: float = Field(description="Database size before reset in MB")
    size_after_mb: float | None = Field(
        default=None,
        description=(
            "Database size after vacuum in MB. Null when vacuum_scheduled=true — the"
            " vacuum is still running as a post-response background task."
        ),
    )
    vacuum_scheduled: bool = Field(
        default=False,
        description=(
            "True when VACUUM has been queued as a post-response background task."
            " size_after_mb will be null in this case."
        ),
    )
    bootstrap_invite_url: str | None = Field(
        default=None,
        description=(
            "Admin invite redemption URL (only present after include_accounts=true reset)."
            " The caller's account was deleted; redeem this URL to create a new admin account."
        ),
    )


class DataRange(BaseModel):
    """Profile data availability — always all-time, unaffected by days_limit."""

    earliest_date: date | None = None
    latest_date: date | None = None


class HealthImportResult(BaseModel):
    """Result of an Apple Health export.xml import operation."""

    inserted: int = Field(default=0, description="Health samples successfully inserted")
    skipped: int = Field(default=0, description="Samples skipped as duplicates")
    unknown_metrics: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Unhandled HealthKit record types from the XML export with their record counts."
        ),
    )
    nights_recomputed: int = Field(
        default=0,
        description="Nightly sleep summaries recomputed (0 on dry_run)",
    )
    dry_run: bool = Field(
        default=False,
        description="True when no writes were performed",
    )


class DeleteDataResult(BaseModel):
    """Result of a per-user delete-all-data operation."""

    status: str = Field(description="Operation status ('success')")
    devices_deleted: int = Field(
        description="Device rows deleted (cascades removed all sleep data)"
    )
    import_jobs_deleted: int = Field(
        description="Import job records deleted for this user"
    )
    profiles_processed: int = Field(
        description="Profiles whose raw backup dirs were purged"
    )
    size_before_mb: float = Field(description="Database size before deletion in MB")
    size_after_mb: float | None = Field(
        default=None,
        description=(
            "Database size after vacuum in MB. Null when vacuum_scheduled=true — the"
            " vacuum is still running as a post-response background task."
        ),
    )
    vacuum_scheduled: bool = Field(
        default=False,
        description=(
            "True when VACUUM has been queued as a post-response background task."
            " size_after_mb will be null in this case."
        ),
    )
