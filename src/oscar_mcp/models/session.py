"""Pydantic models for Session data."""

from typing import Dict, Optional

from pydantic import BaseModel, Field


class SessionSummary(BaseModel):
    """Summary information about a therapy session."""

    id: int
    session_id: str = Field(description="Unique session identifier from OSCAR")
    machine_brand: Optional[str] = None
    machine_model: Optional[str] = None

    # Timing
    start_time: int = Field(description="Session start time (Unix timestamp in milliseconds)")
    end_time: int = Field(description="Session end time (Unix timestamp in milliseconds)")
    duration_hours: float = Field(description="Session duration in hours")

    # Key metrics
    ahi: Optional[float] = Field(default=None, description="Apnea-Hypopnea Index")
    pressure_median: Optional[float] = Field(default=None, description="Median pressure (cmH₂O)")
    leak_median: Optional[float] = Field(default=None, description="Median leak rate (L/min)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "session_id": "20240115_220015",
                "machine_brand": "ResMed",
                "machine_model": "AirSense 10 AutoSet",
                "start_time": 1705357215000,
                "end_time": 1705384815000,
                "duration_hours": 7.67,
                "ahi": 2.3,
                "pressure_median": 10.2,
                "leak_median": 8.5,
            }
        }


class SessionDetail(BaseModel):
    """Detailed information about a therapy session."""

    id: int
    session_id: str
    machine_brand: Optional[str] = None
    machine_model: Optional[str] = None

    # Timing
    start_time: int = Field(description="Unix timestamp in milliseconds")
    end_time: int = Field(description="Unix timestamp in milliseconds")
    duration_hours: float

    # Event counts
    obstructive_apneas: int = 0
    hypopneas: int = 0
    central_apneas: int = 0
    reras: int = 0

    # Indices
    ahi: Optional[float] = None

    # Pressure statistics
    pressure_min: Optional[float] = None
    pressure_max: Optional[float] = None
    pressure_median: Optional[float] = None
    pressure_95th: Optional[float] = None

    # Leak statistics
    leak_median: Optional[float] = None
    leak_95th: Optional[float] = None
    leak_max: Optional[float] = None

    # Respiratory statistics
    resp_rate_avg: Optional[float] = None
    tidal_volume_avg: Optional[float] = None
    minute_vent_avg: Optional[float] = None

    # SpO2 statistics (if available)
    spo2_avg: Optional[float] = None
    spo2_min: Optional[float] = None
    pulse_avg: Optional[float] = None

    # Settings
    settings: Dict[str, str] = Field(default_factory=dict)

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "session_id": "20240115_220015",
                "machine_brand": "ResMed",
                "machine_model": "AirSense 10 AutoSet",
                "start_time": 1705357215000,
                "end_time": 1705384815000,
                "duration_hours": 7.67,
                "obstructive_apneas": 12,
                "hypopneas": 6,
                "central_apneas": 0,
                "reras": 2,
                "ahi": 2.3,
                "pressure_min": 8.0,
                "pressure_max": 14.2,
                "pressure_median": 10.2,
                "pressure_95th": 12.8,
                "leak_median": 8.5,
                "leak_95th": 18.2,
                "leak_max": 24.0,
                "resp_rate_avg": 14.5,
                "tidal_volume_avg": 520,
                "minute_vent_avg": 7.5,
                "settings": {
                    "Mode": "AutoSet",
                    "MinPressure": "8.0",
                    "MaxPressure": "15.0",
                    "EPR": "3",
                },
            }
        }
