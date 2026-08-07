"""Profile lifecycle service.

Handles create, list, rename, set-default, and the offline deletion saga.

Deletion is NOT exposed via the web API — it requires the exclusive writer lease,
which the running API server always holds shared.  The web API only supports
create / list / rename / set-default.  Deletion is CLI-only.
"""

from __future__ import annotations

import logging
import shutil

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.session import session_scope
from snore.services.writer_lease import get_writer_lease

logger = logging.getLogger(__name__)


def purge_profile_raw_dir(profile_id: int, raw_root: Path, label: str = "") -> None:
    """Quarantine-rename then rmtree the raw/<profile_id>/ backup dir (idempotent).

    Follows the two-step pattern used by DeletionSaga: atomic rename to
    .quarantine/ first so the directory is invisible to new imports, then rmtree.
    Interruption leaves the dir in .quarantine/ where startup recovery will clean it.

    Args:
        profile_id: The profile whose raw backup dir to purge.
        raw_root: Root of the raw backup directory tree.
        label: Optional label included in the log message (e.g. "delete-data").
    """
    src = raw_root / str(profile_id)
    if not src.exists():
        return
    quarantine = raw_root / ".quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    dst = quarantine / str(profile_id)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    src.rename(dst)
    shutil.rmtree(dst, ignore_errors=True)
    tag = f" ({label})" if label else ""
    logger.info("Purged raw backup for profile %d%s", profile_id, tag)


class ProfileNotFoundError(Exception):
    pass


class ProfileLastError(Exception):
    """Raised when attempting to delete the last profile."""


class ProfileService:
    """Profile lifecycle management (non-deletion operations).

    Deletion is in DeletionSaga and is CLI-only.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_profiles(self, user_id: int) -> list[models.Profile]:
        """List all live profiles for a user."""
        stmt = (
            select(models.Profile)
            .where(
                models.Profile.user_id == user_id,
                models.Profile.deleting_at.is_(None),
            )
            .order_by(models.Profile.id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def create_profile(self, user_id: int, name: str) -> models.Profile:
        """Create a new profile for a user.

        Also sets it as the user's default if they have none.
        """
        profile = models.Profile(user_id=user_id, name=name)
        self._db.add(profile)
        await self._db.flush()

        # Set as default if user has no default yet.
        user = await self._db.get(models.User, user_id)
        if user is not None and user.default_profile_id is None:
            user.default_profile_id = profile.id

        return profile

    async def rename_profile(
        self, user_id: int, profile_id: int, new_name: str
    ) -> models.Profile:
        """Rename a profile. The new name must be unique per user."""
        profile = await self._get_live(user_id, profile_id)
        profile.name = new_name
        return profile

    async def set_default_profile(
        self, user_id: int, profile_id: int
    ) -> models.Profile:
        """Set a profile as the user's default."""
        profile = await self._get_live(user_id, profile_id)
        user = await self._db.get(models.User, user_id)
        if user is not None:
            user.default_profile_id = profile.id
        return profile

    async def _get_live(self, user_id: int, profile_id: int) -> models.Profile:
        stmt = select(models.Profile).where(
            models.Profile.id == profile_id,
            models.Profile.user_id == user_id,
            models.Profile.deleting_at.is_(None),
        )
        profile = (await self._db.execute(stmt)).scalars().first()
        if profile is None:
            raise ProfileNotFoundError(
                f"Profile {profile_id} not found for user {user_id}"
            )
        return profile


class DeletionSaga:
    """Recoverable profile deletion saga.  CLI-only — requires exclusive writer lease.

    Steps:
        0. Acquire exclusive writer lease (refuses if API or CLI import is running).
        1. DB txn: validate last-profile rules; repoint active/default refs; set deleting_at.
        2. Atomic rename: raw/<profile_id>/ → raw/.quarantine/<profile_id>/
        3. DB txn: cascade-delete device rows + the tombstone profile row.
        4. Idempotent quarantine purge.

    Startup recovery:
        If a committed tombstone is found at startup, re-run steps 2–4 from the
        committed state.  Every step is idempotent.
    """

    def __init__(
        self,
        raw_root: Path | None = None,
    ) -> None:
        from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

        self._raw_root = raw_root or DEFAULT_RAW_BACKUP_DIR

    @property
    def quarantine_root(self) -> Path:
        return self._raw_root / ".quarantine"

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def delete_profile(self, profile_id: int, user_id: int) -> None:
        """Run the full deletion saga under the exclusive writer lease.

        Raises:
            WriterLeaseError: If the API server or a CLI import is running.
            ProfileLastError: If this is the user's last profile.
            ProfileNotFoundError: If the profile doesn't exist.
        """
        import asyncio  # noqa: PLC0415

        lease = get_writer_lease()
        with lease.exclusive():
            # Run async saga steps synchronously (CLI context).
            asyncio.run(self._run_saga(profile_id, user_id))

    def purge_quarantine(self) -> None:
        """Idempotent quarantine purge under the exclusive writer lease."""
        lease = get_writer_lease()
        with lease.exclusive():
            self._purge_quarantine_dir()

    # ------------------------------------------------------------------
    # Startup recovery (called from API lifespan before downgrading to shared)
    # ------------------------------------------------------------------

    def recover(self) -> None:
        """Re-run any interrupted deletion saga found on disk.

        Called during startup under the exclusive lease.  The exclusive lease
        is held by the caller (lifespan) which then downgrades to shared.
        """
        import asyncio  # noqa: PLC0415

        asyncio.run(self._recover_async())

    async def _recover_async(self) -> None:
        """Re-run any interrupted deletion sagas found at startup.

        Two recovery cases:
        1. Tombstone exists (deleting_at IS NOT NULL): the cascade may not have
           finished — re-run steps 2-4 (rename → cascade → purge).
        2. No tombstone but quarantine dir exists: the cascade completed and the
           profile row is gone, but the purge step was interrupted.  Purge the
           quarantine dir directly.
        """
        async with session_scope() as db:
            stmt = select(models.Profile).where(models.Profile.deleting_at.is_not(None))
            tombstoned = list((await db.execute(stmt)).scalars().all())
            tombstoned_ids = {p.id for p in tombstoned}

        for profile in tombstoned:
            logger.info(
                "Startup recovery: finishing interrupted deletion for profile %d",
                profile.id,
            )
            # Step 2: rename (idempotent if already done)
            self._rename_raw_to_quarantine(profile.id)
            # Step 3: cascade-delete
            async with session_scope() as db:
                p = await db.get(models.Profile, profile.id)
                if p is not None:
                    await db.delete(p)
            # Step 4: purge
            self._purge_quarantine_for_profile(profile.id)

        # Case 2: quarantine dirs with no surviving tombstone — cascade completed
        # but purge was interrupted.  Enumerate and purge directly.
        if self.quarantine_root.exists():
            for entry in self.quarantine_root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    profile_id = int(entry.name)
                except ValueError:
                    continue
                if profile_id in tombstoned_ids:
                    # Already handled above.
                    continue
                logger.info(
                    "Startup recovery: purging orphaned quarantine for profile %d",
                    profile_id,
                )
                self._purge_quarantine_for_profile(profile_id)

    # ------------------------------------------------------------------
    # Internal saga steps
    # ------------------------------------------------------------------

    async def _run_saga(self, profile_id: int, user_id: int) -> None:
        """Execute the deletion saga steps in order."""
        # Step 1: tombstone
        await self._step1_tombstone(profile_id, user_id)

        # Step 2: rename
        self._rename_raw_to_quarantine(profile_id)

        # Step 3: cascade-delete
        async with session_scope() as db:
            profile = await db.get(models.Profile, profile_id)
            if profile is not None:
                # Validate deleting_at IS NOT NULL (defense in depth)
                if profile.deleting_at is None:
                    raise RuntimeError(
                        f"Profile {profile_id} tombstone missing at cascade step"
                    )
                await db.delete(profile)

        # Step 4: purge quarantine
        self._purge_quarantine_for_profile(profile_id)

    async def _step1_tombstone(self, profile_id: int, user_id: int) -> None:
        """Validate last-profile rules, repoint refs, set deleting_at."""
        async with session_scope() as db:
            # Count live profiles for this user.
            stmt = select(models.Profile).where(
                models.Profile.user_id == user_id,
                models.Profile.deleting_at.is_(None),
            )
            live_profiles = list((await db.execute(stmt)).scalars().all())

            if len(live_profiles) <= 1:
                raise ProfileLastError(
                    "Cannot delete the last profile. "
                    "Create another profile before deleting this one."
                )

            # Find the target profile.
            target = next((p for p in live_profiles if p.id == profile_id), None)
            if target is None:
                raise ProfileNotFoundError(
                    f"Profile {profile_id} not found for user {user_id}"
                )

            # Choose a fallback profile (first live profile that isn't target).
            fallback = next(p for p in live_profiles if p.id != profile_id)

            # Update user's default_profile_id if it points at the target.
            user = await db.get(models.User, user_id)
            if user is not None and user.default_profile_id == profile_id:
                user.default_profile_id = fallback.id

            # Set the tombstone.
            target.deleting_at = datetime.now(UTC)

    def _rename_raw_to_quarantine(self, profile_id: int) -> None:
        """Atomic same-filesystem rename of raw/<profile_id>/ to quarantine."""
        src = self._raw_root / str(profile_id)
        if not src.exists():
            return  # Already moved or never created.

        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        dst = self.quarantine_root / str(profile_id)
        if dst.exists():
            # Previous incomplete saga — remove old quarantine first.
            shutil.rmtree(dst, ignore_errors=True)
        src.rename(dst)
        logger.info("Renamed raw/%d to quarantine", profile_id)

    def _purge_quarantine_for_profile(self, profile_id: int) -> None:
        """Remove the quarantine directory for a specific profile (idempotent)."""
        dst = self.quarantine_root / str(profile_id)
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
            logger.info("Purged quarantine for profile %d", profile_id)

    def _purge_quarantine_dir(self) -> None:
        """Remove all quarantine directories (operator purge-quarantine command)."""
        if self.quarantine_root.exists():
            shutil.rmtree(self.quarantine_root, ignore_errors=True)
            logger.info("Purged full quarantine directory")
