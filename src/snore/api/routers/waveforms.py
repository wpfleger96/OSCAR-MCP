from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.api.schemas import WaveformDataResponse
from snore.services import WaveformService
from snore.services.schemas import WaveformInfo

router = APIRouter()

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
def list_waveforms(
    session_id: int, db: Session = Depends(get_db)
) -> list[WaveformInfo]:
    service = WaveformService(db)
    return service.list_waveforms(session_id)


@router.get(
    "/{session_id}/waveforms/{waveform_type}", response_model=WaveformDataResponse
)
def get_waveform(
    session_id: int,
    waveform_type: VALID_WAVEFORM_TYPES,
    max_points: int = Query(default=2000),
    start_seconds: float | None = Query(default=None),
    end_seconds: float | None = Query(default=None),
    db: Session = Depends(get_db),
) -> WaveformDataResponse:
    service = WaveformService(db)
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
