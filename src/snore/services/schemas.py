"""Pydantic response schemas for service layer.

These models define the contract between services and consumers (CLI/API).
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

__all__ = [
    "PeriodStatistics",
    "EventValidationResult",
    "DatabaseStats",
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
