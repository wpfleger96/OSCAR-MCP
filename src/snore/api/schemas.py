from __future__ import annotations

from datetime import date
from typing import Literal, cast

from pydantic import BaseModel, Field

from snore.services.schemas import (
    DayDetail,
    DayListItem,
    ImportSource,
    RxAllResponse,
    RxChangesResponse,
    RxComparisonResponse,
    RxPeriodResponse,
    RxSettingChange,
)

DISPLAY_NAME_MAX_LEN: int = 150

__all__ = [
    "DISPLAY_NAME_MAX_LEN",
    "MessageResponse",
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
    "RxAllResponse",
    "RxPeriodResponse",
    "RxComparisonResponse",
    "RxSettingChange",
    "RxChangesResponse",
    "AnalysisJobStatus",
    "AnalysisJobsListResponse",
    "AnalysisJobEnqueued",
    "ImportSourceResultSummary",
    "ImportResultSummary",
    "LinkedAnalysisSummary",
    "PipelineJobStatus",
    "PipelineJobsListResponse",
]


class MessageResponse(BaseModel):
    message: str


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
    primary_mode: str | None = Field(
        default=None,
        description=(
            "Mode whose recovery markers are persisted. "
            "Must be a member of `modes` when supplied; "
            "defaults to 'aasm' when 'aasm' is in modes, required otherwise."
        ),
    )
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
    primary_mode: str | None = Field(
        default=None,
        description=(
            "Mode whose recovery markers are persisted. "
            "Must be a member of `modes` when supplied; "
            "defaults to 'aasm' when 'aasm' is in modes, required otherwise."
        ),
    )
    store_results: bool = True


class ValidationRequest(BaseModel):
    from_date: date
    to_date: date
    mode: AnalysisMode = "aasm"


class AnalysisJobStatus(BaseModel):
    job_id: str
    state: str
    source: str
    session_count: int
    progress_completed: int
    progress_total: int
    error_message: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    owner_user_id: int | None


class AnalysisJobsListResponse(BaseModel):
    jobs: list[AnalysisJobStatus]


class AnalysisJobEnqueued(BaseModel):
    job_id: str
    session_count: int


class ImportSourceResultSummary(BaseModel):
    source: ImportSource
    imported: int
    skipped: int
    failed: int
    warnings: list[str]


class ImportResultSummary(BaseModel):
    """Trimmed ImportResult excluding imported_session_ids (poll-bandwidth)."""

    total_imported: int
    total_skipped: int
    total_failed: int
    warnings: list[str]
    sources: list[ImportSourceResultSummary]


class LinkedAnalysisSummary(BaseModel):
    job_id: str
    state: str
    progress_completed: int
    progress_total: int
    error_message: str | None


class PipelineJobStatus(BaseModel):
    """Stitched view of one import job and its downstream analysis job.

    created_at and finished_at are time.monotonic() floats — ordering only,
    not wall-clock (same semantics as the analysis jobs endpoint).
    """

    job_id: str
    job_type: str
    state: str
    stage: str
    file_count: int
    created_at: float
    finished_at: float | None
    progress_message: str | None
    sessions_imported: int | None
    import_result: ImportResultSummary | None
    error_message: str | None
    analysis_job_id: str | None
    analysis_queued: bool | None
    linked_analysis: LinkedAnalysisSummary | None


class PipelineJobsListResponse(BaseModel):
    jobs: list[PipelineJobStatus]
