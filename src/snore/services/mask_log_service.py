"""Mask equipment log service for per-profile mask history CRUD."""

from collections.abc import Mapping
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.schemas import MaskLogEntryResponse

__all__ = ["MaskLogService"]


class MaskLogService:
    """Service for CRUD on a profile's user-entered mask equipment log."""

    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self.db_session = db_session
        self.profile_id = profile_id

    async def _get_owned_entry(self, entry_id: int) -> models.MaskLogEntry:
        """Return the entry, raising NotFoundError if missing or foreign.

        Foreign ID → 404, not 403, to avoid oracle attacks (matches DeviceService).
        """
        entry = (
            (
                await self.db_session.execute(
                    select(models.MaskLogEntry).where(
                        models.MaskLogEntry.id == entry_id,
                        models.MaskLogEntry.profile_id == self.profile_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if not entry:
            raise NotFoundError(f"Mask log entry {entry_id} not found")
        return entry

    async def list_entries(self) -> list[MaskLogEntryResponse]:
        """List all mask log entries for this profile ordered by start date."""
        entries = (
            (
                await self.db_session.execute(
                    select(models.MaskLogEntry)
                    .where(models.MaskLogEntry.profile_id == self.profile_id)
                    .order_by(models.MaskLogEntry.start_date, models.MaskLogEntry.id)
                )
            )
            .scalars()
            .all()
        )
        return [MaskLogEntryResponse.model_validate(e) for e in entries]

    async def create_entry(
        self,
        *,
        brand: str,
        model: str,
        style: str,
        start_date: date,
        size: str | None = None,
        notes: str | None = None,
    ) -> MaskLogEntryResponse:
        """Create a mask log entry for this profile."""
        entry = models.MaskLogEntry(
            profile_id=self.profile_id,
            brand=brand,
            model=model,
            style=style,
            start_date=start_date,
            size=size,
            notes=notes,
        )
        self.db_session.add(entry)
        await self.db_session.flush()
        return MaskLogEntryResponse.model_validate(entry)

    async def update_entry(
        self, entry_id: int, updates: Mapping[str, str | date | None]
    ) -> MaskLogEntryResponse:
        """Apply the given field updates to an entry (PATCH semantics).

        ``updates`` maps column names to new values — pass only the fields the
        caller explicitly set so omitted fields stay unchanged.

        Raises NotFoundError if the entry doesn't exist or belongs to a
        different profile.
        """
        entry = await self._get_owned_entry(entry_id)
        for key, value in updates.items():
            setattr(entry, key, value)
        await self.db_session.flush()
        return MaskLogEntryResponse.model_validate(entry)

    async def get_active_entry_for_date(
        self, target_date: date
    ) -> MaskLogEntryResponse | None:
        """Return the mask entry active on target_date.

        "Active" means the most recent entry with start_date <= target_date,
        tie-broken by id DESC for same-day entries. None when no entry qualifies.
        """
        entry = (
            (
                await self.db_session.execute(
                    select(models.MaskLogEntry)
                    .where(
                        models.MaskLogEntry.profile_id == self.profile_id,
                        models.MaskLogEntry.start_date <= target_date,
                    )
                    .order_by(
                        models.MaskLogEntry.start_date.desc(),
                        models.MaskLogEntry.id.desc(),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if not entry:
            return None
        return MaskLogEntryResponse.model_validate(entry)

    async def delete_entry(self, entry_id: int) -> None:
        """Delete an entry. Raises NotFoundError if missing or foreign."""
        entry = await self._get_owned_entry(entry_id)
        await self.db_session.delete(entry)
        await self.db_session.flush()
