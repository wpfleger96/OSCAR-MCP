"""Waveform service for listing and loading waveform data."""

from typing import Any

import numpy as np

from sqlalchemy.orm import Session

from snore.analysis.data.waveform_loader import WaveformLoader
from snore.database import models
from snore.services.lttb import lttb_downsample
from snore.services.schemas import WaveformInfo

__all__ = ["WaveformService"]


class WaveformService:
    """Service for waveform listing and loading operations."""

    def __init__(self, db_session: Session):
        """
        Initialize waveform service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    def list_waveforms(self, session_id: int) -> list[WaveformInfo]:
        """
        List available waveform types for a session.

        Returns empty list if no waveforms found.

        Args:
            session_id: Database session ID

        Returns:
            List of WaveformInfo objects with metadata
        """
        waveforms = (
            self.db_session.query(models.Waveform)
            .filter(models.Waveform.session_id == session_id)
            .order_by(models.Waveform.waveform_type)
            .all()
        )

        result = []
        for wf in waveforms:
            sample_count = wf.sample_count or 0
            duration_seconds = (
                sample_count / wf.sample_rate if wf.sample_rate > 0 else 0
            )
            result.append(
                WaveformInfo(
                    waveform_type=wf.waveform_type,
                    sample_rate=wf.sample_rate,
                    sample_count=sample_count,
                    unit=wf.unit,
                    duration_hours=duration_seconds / 3600,
                )
            )
        return result

    def get_waveform_data(
        self,
        session_id: int,
        waveform_type: str,
        max_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        """
        Load waveform data with optional LTTB downsampling.

        Args:
            session_id: Database session ID
            waveform_type: Type of waveform to load
            max_points: If set, downsample to this many points using LTTB

        Returns:
            Tuple of (timestamps, values, metadata)

        Raises:
            ValueError: If waveform not found
        """
        loader = WaveformLoader(self.db_session)
        timestamps, values, metadata = loader.load_waveform(
            session_id=session_id,
            waveform_type=waveform_type,
            apply_filter=False,
        )

        if max_points and len(timestamps) > max_points:
            timestamps, values = lttb_downsample(timestamps, values, max_points)

        return timestamps, values, metadata
