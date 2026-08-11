"""Mask epoch service: contiguous device-reported mask-type periods."""

from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.rx_tracker import RxTracker
from snore.services.schemas import MaskEpochResponse

__all__ = ["MaskEpochService"]

# Maps device-reported mask_type values to the normalized mask_log style vocabulary.
# Device values not present here map to style=None.
_MASK_TYPE_TO_STYLE: dict[str, str] = {
    "Pillows": "pillows",
    "Nasal": "nasal",
    "Full Face": "full_face",
}


class MaskEpochService:
    """Service for querying contiguous device-reported mask-type epochs."""

    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self.db_session = db_session
        self.profile_id = profile_id

    async def list_epochs(self) -> list[MaskEpochResponse]:
        """Return contiguous device mask-type epochs in chronological order.

        Each epoch is a run of consecutive nights where the device reported the
        same mask_type.  The style field normalizes the device value to the
        mask_log vocabulary; it is None for unrecognized device values (e.g.
        "Unknown").
        """
        periods = await RxTracker(self.profile_id).get_history(
            self.db_session, keys=("mask_type",)
        )
        return [
            MaskEpochResponse(
                mask_type=p.settings["mask_type"],
                style=_MASK_TYPE_TO_STYLE.get(p.settings["mask_type"]),
                start_date=p.start_date,
                end_date=p.end_date,
                days_count=p.days_count,
                device_name=p.device_name,
            )
            for p in periods
            if "mask_type" in p.settings
        ]
