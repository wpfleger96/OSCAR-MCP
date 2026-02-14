"""Pydantic response schemas for service layer.

These models define the contract between services and consumers (CLI/API).
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

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
    "AnalysisDeletePreview",
    "EventMatchResult",
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

    class Config:
        json_schema_extra = {
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


class AnalysisDeletePreview(BaseModel):
    """Preview of analysis data to be deleted."""

    sessions_with_analysis: int
    total_analysis_records: int
    records_to_delete: int
    patterns_count: int
    session_details: list[dict[str, Any]] = Field(default_factory=list)
