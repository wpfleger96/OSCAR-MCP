"""Unit tests for DemoService.

Covers demo_user_exists, demo_data_exists, ensure_user_and_profile, and the
early-return path of import_from_fixtures against a real in-memory database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from snore.database import models
from snore.services.demo_service import DEMO_EMAIL, DemoService

# ---------------------------------------------------------------------------
# demo_user_exists
# ---------------------------------------------------------------------------


class TestDemoUserExists:
    async def test_no_user_returns_false(self, async_db_session):
        result = await DemoService(async_db_session).demo_user_exists()

        assert result is False

    async def test_active_demo_user_returns_true(self, async_db_session):
        user = models.User(
            canonical_email=DEMO_EMAIL,
            role="demo",
            display_name="Demo",
            password_hash=None,
            session_version=0,
        )
        async_db_session.add(user)
        await async_db_session.flush()

        result = await DemoService(async_db_session).demo_user_exists()

        assert result is True

    async def test_disabled_demo_user_returns_false(self, async_db_session):
        user = models.User(
            canonical_email=DEMO_EMAIL,
            role="demo",
            password_hash=None,
            session_version=0,
            disabled_at=datetime.now(UTC),
        )
        async_db_session.add(user)
        await async_db_session.flush()

        result = await DemoService(async_db_session).demo_user_exists()

        assert result is False


# ---------------------------------------------------------------------------
# demo_data_exists
# ---------------------------------------------------------------------------


class TestDemoDataExists:
    async def test_no_user_returns_false(self, async_db_session):
        result = await DemoService(async_db_session).demo_data_exists()

        assert result is False

    async def test_demo_email_user_with_no_sessions_returns_false(
        self, async_db_session
    ):
        user = models.User(
            canonical_email=DEMO_EMAIL,
            role="demo",
            password_hash=None,
            session_version=0,
        )
        async_db_session.add(user)
        await async_db_session.flush()

        result = await DemoService(async_db_session).demo_data_exists()

        assert result is False

    async def test_demo_email_user_with_one_session_returns_true(
        self, async_db_session
    ):
        # Build the full User → Profile → Device → Session chain.
        user = models.User(
            canonical_email=DEMO_EMAIL,
            role="demo",
            password_hash=None,
            session_version=0,
        )
        async_db_session.add(user)
        await async_db_session.flush()

        profile = models.Profile(user_id=user.id, name="Demo")
        async_db_session.add(profile)
        await async_db_session.flush()

        device = models.Device(
            profile_id=profile.id,
            manufacturer="Test",
            model="Test Device",
            serial_number="DEMO-TEST-001",
        )
        async_db_session.add(device)
        await async_db_session.flush()

        now = datetime.now()
        session = models.Session(
            device_id=device.id,
            device_session_id="demo_session_001",
            start_time=now,
            end_time=now + timedelta(hours=8),
        )
        async_db_session.add(session)
        await async_db_session.flush()

        result = await DemoService(async_db_session).demo_data_exists()

        assert result is True


# ---------------------------------------------------------------------------
# ensure_user_and_profile
# ---------------------------------------------------------------------------


class TestEnsureUserAndProfile:
    async def test_fresh_db_creates_user_profile_and_sets_default_profile_id(
        self, async_db_session
    ):
        user, profile, created = await DemoService(
            async_db_session
        ).ensure_user_and_profile()

        assert created is True
        assert user.id is not None
        assert profile.id is not None
        assert user.canonical_email == DEMO_EMAIL
        assert user.role == "demo"
        assert profile.name == "Demo"
        assert user.default_profile_id == profile.id

    async def test_second_call_returns_same_ids_and_created_false(
        self, async_db_session
    ):
        svc = DemoService(async_db_session)
        user1, profile1, created1 = await svc.ensure_user_and_profile()
        user2, profile2, created2 = await svc.ensure_user_and_profile()

        assert created1 is True
        assert created2 is False
        assert user2.id == user1.id
        assert profile2.id == profile1.id

    async def test_rerun_with_existing_devices_deletes_devices_and_resets_profile_fields(
        self, async_db_session
    ):
        from sqlalchemy import select  # noqa: PLC0415

        svc = DemoService(async_db_session)
        user, profile, _ = await svc.ensure_user_and_profile()

        # Seed a device and set some profile fields before the re-run.
        device = models.Device(
            profile_id=profile.id,
            manufacturer="ResMed",
            model="AirSense 11",
            serial_number="DEMO-DEVICE-001",
        )
        async_db_session.add(device)
        profile.username = "demo_user"
        profile.height_cm = 180
        profile.settings = {"cpap_pressure": 12}
        await async_db_session.flush()

        # Re-run: existing profile is found, devices deleted, fields reset.
        user2, profile2, created2 = await svc.ensure_user_and_profile()

        assert created2 is False
        assert profile2.id == profile.id
        assert profile2.username is None
        assert profile2.height_cm is None
        assert profile2.settings == {}

        remaining = (
            (
                await async_db_session.execute(
                    select(models.Device).where(models.Device.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


# ---------------------------------------------------------------------------
# import_from_fixtures — empty-dir early-return path
# ---------------------------------------------------------------------------


class TestImportFromFixtures:
    async def test_empty_dir_returns_zero_counts(self, async_db_session, tmp_path):
        result = await DemoService(async_db_session).import_from_fixtures(tmp_path)

        assert result == {"sessions": 0, "skipped": 0, "failed": 0}

    async def test_empty_dir_still_creates_demo_user_before_early_return(
        self, async_db_session, tmp_path
    ):
        # ensure_user_and_profile is called before the grouped-files check, so a
        # demo user exists even when the fixtures directory is empty.
        svc = DemoService(async_db_session)
        await svc.import_from_fixtures(tmp_path)

        user_exists = await svc.demo_user_exists()
        assert user_exists is True
