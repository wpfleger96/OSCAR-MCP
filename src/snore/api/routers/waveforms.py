from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from snore.api.deps import service_dep
from snore.api.schemas import WaveformDataResponse
from snore.services import WaveformService
from snore.services.schemas import EventComparisonResult, WaveformInfo

router = APIRouter()

WaveformServiceDep = Annotated[WaveformService, Depends(service_dep(WaveformService))]

VALID_WAVEFORM_TYPES = Literal[
    "flow",
    "pressure",
    "therapy_pressure",
    "epap",
    "leak",
    "mv",
    "rr",
    "tv",
    "spo2",
    "pulse",
    "fl",
    "snore",
]


@router.get("/{session_id}/waveforms", response_model=list[WaveformInfo])
def list_waveforms(session_id: int, service: WaveformServiceDep) -> list[WaveformInfo]:
    return service.list_waveforms(session_id)


@router.get("/{session_id}/waveforms/compare", response_model=EventComparisonResult)
def compare_waveform_events(
    session_id: int,
    service: WaveformServiceDep,
    mode: Literal["aasm", "aasm_relaxed", "resmed"] = Query(default="aasm"),
) -> EventComparisonResult:
    return service.compare_events(session_id, mode=mode)


@router.get(
    "/{session_id}/waveforms/{waveform_type}", response_model=WaveformDataResponse
)
def get_waveform(
    session_id: int,
    waveform_type: VALID_WAVEFORM_TYPES,
    service: WaveformServiceDep,
    max_points: int = Query(default=2000),
    start_seconds: float | None = Query(default=None),
    end_seconds: float | None = Query(default=None),
) -> WaveformDataResponse:
    timestamps, values, metadata = service.get_waveform_data(
        session_id=session_id,
        waveform_type=waveform_type,
        max_points=max_points,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )

    total_samples = metadata.get("sample_count", len(timestamps))
    returned = len(timestamps)

    return WaveformDataResponse(
        timestamps=timestamps.tolist(),
        values=values.tolist(),
        sample_rate=metadata.get("sample_rate", 0.0),
        unit=metadata.get("unit", "") or "",
        total_samples=total_samples,
        downsampled=returned < total_samples,
        returned_samples=returned,
    )
