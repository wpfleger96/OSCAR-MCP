from __future__ import annotations

from pydantic import BaseModel, Field

from snore.services.schemas import (
    DayDetail,
    DayListItem,
    RxComparisonResponse,
    RxPeriodResponse,
)

__all__ = [
    "PaginatedResponse",
    "WaveformDataResponse",
    "SessionEnabledRequest",
    "SessionDeleteRequest",
    "AnalysisRunRequest",
    "AnalysisDeleteRequest",
    "EventItem",
    "DayDetail",
    "DayListItem",
    "RxPeriodResponse",
    "RxComparisonResponse",
]


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class WaveformDataResponse(BaseModel):
    timestamps: list[float]
    values: list[float]
    sample_rate: float
    unit: str
    total_samples: int
    downsampled: bool
    returned_samples: int


class SessionEnabledRequest(BaseModel):
    enabled: bool


class SessionDeleteRequest(BaseModel):
    session_ids: list[int]


class AnalysisRunRequest(BaseModel):
    modes: list[str] = Field(default_factory=lambda: ["aasm"])
    store_results: bool = True


class AnalysisDeleteRequest(BaseModel):
    session_ids: list[int] = Field(default_factory=list)
    all_versions: bool = False


class EventItem(BaseModel):
    id: int
    event_type: str
    start_time: float
    duration_seconds: float
    offset_seconds: float
