"""Demo user management — creation, fixture import, and existence checks."""

from __future__ import annotations

import asyncio
import logging

from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.parsers.unified import DeviceInfo, UnifiedSession

logger = logging.getLogger(__name__)

DEMO_EMAIL = "demo@snore.local"
_DEMO_SERIAL_PREFIX = "DEMO"


class DemoService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def demo_user_exists(self) -> bool:
        """True if an active demo user exists (for the ``demo_available`` flag).

        Deliberately mirrors the demo-login route's lookup (role == "demo" AND
        not disabled — see auth.py demo_login) so the button shows iff
        demo-login can succeed.
        """
        q = (
            select(models.User.id)
            .where(
                models.User.role == "demo",
                models.User.disabled_at.is_(None),
            )
            .limit(1)
        )
        return (await self._db.execute(q)).first() is not None

    async def demo_data_exists(self) -> bool:
        """True if a demo user exists AND has at least one imported session.

        Uses ``canonical_email == DEMO_EMAIL`` because it mirrors
        ``ensure_user_and_profile``'s find-or-create key.  The predicates differ
        on purpose: ``demo_user_exists`` checks role+disabled, ``demo_data_exists``
        checks email (the canonical identity anchor).
        """
        user_id = (
            await self._db.execute(
                select(models.User.id).where(models.User.canonical_email == DEMO_EMAIL)
            )
        ).scalar_one_or_none()
        if user_id is None:
            return False

        return (
            await self._db.execute(
                select(literal(1))
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .join(models.Profile, models.Device.profile_id == models.Profile.id)
                .where(models.Profile.user_id == user_id)
                .limit(1)
            )
        ).first() is not None

    async def ensure_user_and_profile(
        self,
    ) -> tuple[models.User, models.Profile, bool]:
        """Find-or-create the demo user and profile.

        Returns ``(user, profile, created)`` where ``created`` is True when the
        demo USER row was newly inserted (False means an existing user was found).

        On re-run with existing data, cascade-deletes old devices so the caller
        can re-import fresh fixture data.
        """
        user = (
            (
                await self._db.execute(
                    select(models.User).where(
                        models.User.canonical_email == DEMO_EMAIL,
                    )
                )
            )
            .scalars()
            .first()
        )

        created = user is None
        if user is None:
            user = models.User(
                canonical_email=DEMO_EMAIL,
                display_name="Demo",
                role="demo",
                password_hash=None,
                session_version=0,
            )
            self._db.add(user)
            await self._db.flush()
            logger.info("Created demo user (id=%d)", user.id)

        profile = (
            (
                await self._db.execute(
                    select(models.Profile).where(
                        models.Profile.user_id == user.id,
                        models.Profile.name == "Demo",
                    )
                )
            )
            .scalars()
            .first()
        )

        if profile is None:
            profile = models.Profile(
                user_id=user.id,
                name="Demo",
                username=None,
                first_name=None,
                last_name=None,
                date_of_birth=None,
                height_cm=None,
                settings={},
            )
            self._db.add(profile)
            await self._db.flush()
            logger.info("Created demo profile (id=%d)", profile.id)
        else:
            profile.username = None
            profile.height_cm = None
            profile.settings = {}
            existing_devices = (
                (
                    await self._db.execute(
                        select(models.Device).where(
                            models.Device.profile_id == profile.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for dev in existing_devices:
                await self._db.delete(dev)
            await self._db.flush()

        if user.default_profile_id != profile.id:
            user.default_profile_id = profile.id
            await self._db.flush()

        return user, profile, created

    async def import_from_fixtures(
        self,
        fixtures_dir: Path,
    ) -> dict[str, int]:
        """Parse bundled EDF fixtures and import them into the demo profile.

        Uses ``group_session_files`` + ``parse_night_session`` to correctly
        handle multi-segment nights (mask removals, bathroom breaks).

        Parsing is bounded to 4 concurrent threads (Semaphore); all DB imports
        are serialized on the single AsyncSession via a Lock.

        Returns a dict with keys: ``sessions``, ``skipped``, ``failed``.
        """
        from snore.database.importers import SessionImporter  # noqa: PLC0415
        from snore.parsers.resmed_edf import ResmedEDFParser  # noqa: PLC0415
        from snore.parsers.resmed_file_index import group_session_files  # noqa: PLC0415

        _user, profile, _created = await self.ensure_user_and_profile()

        device_info = DeviceInfo(
            manufacturer="ResMed",
            model="Demo Device",
            serial_number=f"{_DEMO_SERIAL_PREFIX}-001",
            firmware_version=None,
            hardware_version=None,
            product_code=None,
        )

        grouped = await asyncio.to_thread(group_session_files, fixtures_dir)
        if not grouped:
            logger.warning("No EDF sessions found in %s", fixtures_dir)
            return {"sessions": 0, "skipped": 0, "failed": 0}

        # Drive day_offset from night-date keys (no parse needed up front).
        # Night-date and session start date can differ by ≤1 day across the noon
        # cutoff; the offset is uniform so relative spacing is preserved and the
        # today-7 target is approximate by design.
        day_offset = _compute_day_offset(max(grouped))

        parser = ResmedEDFParser()
        importer = SessionImporter(profile_id=profile.id)

        parse_sem = asyncio.Semaphore(4)  # bound concurrent EDF parse threads
        db_lock = (
            asyncio.Lock()
        )  # AsyncSession is not concurrency-safe; serialize writes

        imported = skipped = failed = 0

        async def _process_night(
            night_date: str,
            segments: dict[str, dict[str, Path]],
        ) -> None:
            nonlocal imported, skipped, failed

            async with parse_sem:
                try:
                    session = await asyncio.to_thread(
                        parser.parse_night_session,
                        night_date=night_date,
                        segments=segments,
                        device_info=device_info,
                        base_path=fixtures_dir,
                    )
                except Exception:
                    logger.warning(
                        "Failed to parse fixture night %s",
                        night_date,
                        exc_info=True,
                    )
                    return

            if session is None:
                return

            shifted = _shift_session(session, day_offset)

            async with db_lock:
                i, s, f, _ids = await importer.import_sessions_batch(
                    [shifted],
                    force=True,
                    db=self._db,
                )
                imported += i
                skipped += s
                failed += f

        await asyncio.gather(
            *[
                _process_night(night_date, segments)
                for night_date, segments in sorted(grouped.items())
            ]
        )

        return {"sessions": imported, "skipped": skipped, "failed": failed}


def _compute_day_offset(most_recent_night_key: str) -> timedelta:
    """Compute a whole-day offset so the most recent night maps to today - 7.

    Night-date keys and session start dates can differ by ≤1 day across the noon
    cutoff; the offset is uniform so relative spacing is preserved and the today-7
    target is approximate by design.
    """
    most_recent = datetime.strptime(most_recent_night_key, "%Y%m%d").date()
    target = date.today() - timedelta(days=7)
    return timedelta(days=(target - most_recent).days)


def _shift_session(session: UnifiedSession, offset: timedelta) -> UnifiedSession:
    """Return a copy of *session* with all timestamps shifted by *offset*."""
    shifted_events = [
        evt.model_copy(
            update={
                "start_time": evt.start_time + offset,
                "end_time": (evt.end_time + offset) if evt.end_time else None,
            }
        )
        for evt in session.events
    ]
    return session.model_copy(
        update={
            "start_time": session.start_time + offset,
            "end_time": session.end_time + offset,
            "events": shifted_events,
            "import_source": "demo",
        }
    )
