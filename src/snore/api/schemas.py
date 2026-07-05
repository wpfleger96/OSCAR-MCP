from __future__ import annotations

from datetime import date
from typing import Literal, cast

from pydantic import BaseModel, Field

from snore.services.schemas import (
    DayDetail,
    DayListItem,
    RxComparisonResponse,
    RxPeriodResponse,
)

__all__ = [
    "AnalysisMode",
    "PaginatedResponse",
    "WaveformDataResponse",
    "SessionEnabledRequest",
    "SessionDeleteRequest",
    "BulkDeletePreviewRequest",
    "AnalysisRunRequest",
    "AnalysisDeleteRequest",
    "BatchAnalysisRequest",
    "ValidationRequest",
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


class BulkDeletePreviewRequest(BaseModel):
    session_ids: list[int] | None = None
    device: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    delete_all: bool = False


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
    spo2_drop: float | None = None
    peak_flow_limitation: float | None = None


AnalysisMode = Literal["aasm", "aasm_relaxed", "resmed"]


class BatchAnalysisRequest(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    modes: list[AnalysisMode] = Field(
        default_factory=lambda: cast(list[AnalysisMode], ["aasm"])
    )
    store_results: bool = True
    max_sessions: int = Field(default=1000, le=10000, ge=1)


class ValidationRequest(BaseModel):
    from_date: date
    to_date: date
    mode: AnalysisMode = "aasm"
