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
    # RX / Day / Event schemas (consumed by RxService, DayService, and API routers)
    "DayListItem",
    "DayDetail",
    "RxPeriodResponse",
    "RxComparisonResponse",
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
    waveform_coverage_pct: float = Field(
        description="Percentage of sessions with waveforms"
    )
    event_coverage_pct: float = Field(description="Percentage of sessions with events")
    analysis_coverage_pct: float = Field(description="Percentage of sessions analyzed")
    first_session: datetime | None = Field(
        default=None, description="Earliest session date"
    )
    last_session: datetime | None = Field(
        default=None, description="Latest session date"
    )


class SessionListItem(BaseModel):
    """Single session item in a list view."""

    id: int = Field(description="Session database ID")
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
    pressure_95th: float | None = None
    epap_mean: float | None = None
    epap_min: float | None = None
    epap_max: float | None = None
    epap_95th: float | None = None
    ipap_median: float | None = None
    ipap_95th: float | None = None
    ipap_max: float | None = None
    leak_mean: float | None = None
    leak_percentile_70: float | None = None
    leak_95th: float | None = None
    spo2_mean: float | None = None
    spo2_min: float | None = None
    spo2_time_below_90: int | None = None
    pulse_mean: float | None = None
    pulse_min: float | None = None
    pulse_max: float | None = None
    respiratory_rate_mean: float | None = None
    tidal_volume_mean: float | None = None
    minute_ventilation_mean: float | None = None


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
    statistics: SessionStatistics | None = None
    settings: list[SessionSetting] | None = None


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

    id: int
    manufacturer: str
    model: str
    serial_number: str


class DayListItem(BaseModel):
    """Summary of a single therapy day."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    device_id: int
    session_count: int
    total_therapy_hours: float | None = None
    ahi: float | None = None


class DayDetail(DayListItem):
    """Full detail of a therapy day including per-metric stats."""

    oai: float | None = None
    cai: float | None = None
    hi: float | None = None
    avg_pressure: float | None = None
    avg_leak: float | None = None
    avg_spo2: float | None = None
    session_ids: list[int] = Field(default_factory=list)


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


class RxComparisonResponse(BaseModel):
    """RX period comparison result with best/worst indices."""

    periods: list[RxPeriodResponse]
    best_index: int | None = None
    worst_index: int | None = None


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


class ImportResult(BaseModel):
    """Aggregate result of an import operation across all sources."""

    total_imported: int = Field(default=0, description="Total sessions imported")
    total_skipped: int = Field(default=0, description="Total sessions skipped")
    total_failed: int = Field(default=0, description="Total sessions that failed")
    sources: list[ImportSourceResult] = Field(
        default_factory=list, description="Per-source results"
    )
    warnings: list[str] = Field(default_factory=list, description="Global warnings")


class BatchSessionResult(BaseModel):
    """Result of analyzing a single session in a batch."""

    session_id: int = Field(description="Session database ID")
    session_date: date | None = Field(default=None, description="Session date")
    success: bool = Field(description="Whether analysis succeeded")
    error: str | None = Field(default=None, description="Error message if failed")


class BatchAnalysisResult(BaseModel):
    """Aggregate result of batch analysis across multiple sessions."""

    total: int = Field(description="Total sessions processed")
    successful: int = Field(default=0, description="Sessions analyzed successfully")
    failed: int = Field(default=0, description="Sessions that failed analysis")
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
