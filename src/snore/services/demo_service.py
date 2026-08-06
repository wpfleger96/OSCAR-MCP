"""Demo user management — creation, fixture import, and existence checks."""

from __future__ import annotations

import asyncio
import logging

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.parsers.unified import DeviceInfo, UnifiedSession

logger = logging.getLogger(__name__)

DEMO_EMAIL = "demo@snore.local"
_DEMO_SERIAL_PREFIX = "DEMO"


class DemoService:
    @staticmethod
    async def demo_user_exists(db: AsyncSession) -> bool:
        """True if an active demo user exists (for the ``demo_available`` flag)."""
        q = (
            select(func.count())
            .select_from(models.User)
            .where(
                models.User.role == "demo",
                models.User.disabled_at.is_(None),
            )
        )
        return (await db.execute(q)).scalar_one() > 0

    @staticmethod
    async def demo_data_exists(db: AsyncSession) -> bool:
        """True if a demo user exists AND has at least one imported session."""
        user = (
            (
                await db.execute(
                    select(models.User).where(models.User.canonical_email == DEMO_EMAIL)
                )
            )
            .scalars()
            .first()
        )
        if user is None:
            return False

        session_count = (
            await db.execute(
                select(func.count())
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .join(models.Profile, models.Device.profile_id == models.Profile.id)
                .where(models.Profile.user_id == user.id)
            )
        ).scalar_one()
        return session_count > 0

    @staticmethod
    async def ensure_user_and_profile(
        db: AsyncSession,
    ) -> tuple[models.User, models.Profile]:
        """Find-or-create the demo user and profile.

        On re-run with existing data, cascade-deletes old devices so the caller
        can re-import fresh fixture data.
        """
        user = (
            (
                await db.execute(
                    select(models.User).where(
                        models.User.canonical_email == DEMO_EMAIL,
                    )
                )
            )
            .scalars()
            .first()
        )

        if user is None:
            user = models.User(
                canonical_email=DEMO_EMAIL,
                display_name="Demo",
                role="demo",
                password_hash=None,
                session_version=0,
            )
            db.add(user)
            await db.flush()
            logger.info("Created demo user (id=%d)", user.id)

        profile = (
            (
                await db.execute(
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
            db.add(profile)
            await db.flush()
            logger.info("Created demo profile (id=%d)", profile.id)
        else:
            profile.username = None
            profile.height_cm = None
            profile.settings = {}
            existing_devices = (
                (
                    await db.execute(
                        select(models.Device).where(
                            models.Device.profile_id == profile.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for dev in existing_devices:
                await db.delete(dev)
            await db.flush()

        if user.default_profile_id != profile.id:
            user.default_profile_id = profile.id
            await db.flush()

        return user, profile

    @classmethod
    async def import_from_fixtures(
        cls,
        db: AsyncSession,
        fixtures_dir: Path,
    ) -> dict[str, int]:
        """Parse bundled EDF fixtures and import them into the demo profile.

        Uses ``group_session_files`` + ``_parse_night_session`` to correctly
        handle multi-segment nights (mask removals, bathroom breaks).

        Returns a dict with keys: ``sessions``, ``skipped``, ``failed``.
        """
        from snore.database.importers import SessionImporter  # noqa: PLC0415
        from snore.parsers.resmed_edf import ResmedEDFParser  # noqa: PLC0415
        from snore.parsers.resmed_file_index import group_session_files  # noqa: PLC0415

        _user, profile = await cls.ensure_user_and_profile(db)

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

        parser = ResmedEDFParser()
        parsed_sessions: list[UnifiedSession] = []

        for night_date in sorted(grouped):
            segments = grouped[night_date]
            try:
                session = await asyncio.to_thread(
                    parser._parse_night_session,
                    night_date=night_date,
                    segments=segments,
                    device_info=device_info,
                    base_path=fixtures_dir,
                )
                if session is not None:
                    parsed_sessions.append(session)
            except Exception:
                logger.warning(
                    "Failed to parse fixture night %s",
                    night_date,
                    exc_info=True,
                )

        if not parsed_sessions:
            logger.warning("No sessions parsed from fixtures")
            return {"sessions": 0, "skipped": 0, "failed": 0}

        day_offset = _compute_day_offset(parsed_sessions)
        shifted = [_shift_session(s, day_offset) for s in parsed_sessions]

        importer = SessionImporter(profile_id=profile.id)
        imported, skipped, failed, _ids = await importer.import_sessions_batch(
            shifted,
            force=True,
            db=db,
        )

        return {"sessions": imported, "skipped": skipped, "failed": failed}


def _compute_day_offset(sessions: list[UnifiedSession]) -> timedelta:
    """Compute a whole-day offset so the most recent session maps to today - 7."""
    most_recent = max(s.start_time.date() for s in sessions)
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
