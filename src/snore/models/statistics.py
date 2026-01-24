"""Pydantic models for Statistics and aggregate data."""

from datetime import date

from pydantic import BaseModel, Field


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


class TherapySummary(BaseModel):
    """Overall therapy summary text report."""

    profile_name: str
    period_start: date
    period_end: date
    summary: str = Field(description="Human-readable summary of therapy")

    class Config:
        json_schema_extra = {
            "example": {
                "profile_name": "John Doe",
                "period_start": "2024-01-01",
                "period_end": "2024-01-31",
                "summary": "John Doe used CPAP therapy for 29 out of 31 days in January 2024. "
                "Average nightly usage was 7.2 hours with a total of 208.5 hours. "
                "The average AHI was 2.8 events per hour, indicating excellent therapy control. "
                "Pressure averaged 10.5 cmH₂O with good mask seal (average leak 9.2 L/min). "
                "SpO₂ levels remained healthy with an average of 96.2%. "
                "This represents highly effective therapy with strong adherence.",
            }
        }
