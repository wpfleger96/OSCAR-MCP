"""Unit tests for profile-scoped service isolation (isolation matrix).

Every service scopes its queries to the caller's profile_id.  These tests
verify the invariants at service call boundaries:

- list operations return empty when records belong to a different profile
- point-lookup operations raise NotFoundError for foreign IDs
- the caller's own records are always returned correctly
"""

from __future__ import annotations

import uuid

from datetime import datetime

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.device_service import DeviceService
from snore.services.session_service import SessionService

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


async def _make_profile(session: AsyncSession) -> models.Profile:
    """Create an isolated User + Profile pair and return the Profile."""
    user = models.User(
        canonical_email=f"user_{uuid.uuid4().hex[:8]}@example.com",
        role="admin",
    )
    session.add(user)
    await session.flush()
    profile = models.Profile(user_id=user.id, name="Default")
    session.add(profile)
    await session.flush()
    return profile


async def _make_device(session: AsyncSession, profile_id: int) -> models.Device:
    """Create a Device owned by profile_id."""
    device = models.Device(
        profile_id=profile_id,
        manufacturer="TestMfg",
        model="TestModel",
        serial_number=f"SN_{uuid.uuid4().hex[:8]}",
    )
    session.add(device)
    await session.flush()
    return device


async def _make_session(session: AsyncSession, device_id: int) -> models.Session:
    """Create a minimal enabled Session for device_id."""
    start = datetime(2025, 1, 1, 22, 0, 0)
    s = models.Session(
        device_id=device_id,
        device_session_id=f"sess_{uuid.uuid4().hex[:8]}",
        start_time=start,
        end_time=datetime(2025, 1, 2, 6, 0, 0),
        duration_seconds=8 * 3600,
        enabled=True,
    )
    session.add(s)
    await session.flush()
    return s


# ---------------------------------------------------------------------------
# DeviceService isolation
# ---------------------------------------------------------------------------


class TestDeviceServiceIsolation:
    """DeviceService respects profile_id boundaries."""

    async def test_list_devices_excludes_foreign_profile(self, async_db_session):
        """list_devices with profile A returns empty when devices belong to profile B."""
        profile_a = await _make_profile(async_db_session)
        profile_b = await _make_profile(async_db_session)
        await _make_device(async_db_session, profile_b.id)

        service = DeviceService(async_db_session, profile_a.id)
        devices = await service.list_devices()
        assert devices == []

    async def test_list_devices_includes_own_profile(self, async_db_session):
        """list_devices returns the profile's own devices."""
        profile = await _make_profile(async_db_session)
        device = await _make_device(async_db_session, profile.id)

        service = DeviceService(async_db_session, profile.id)
        devices = await service.list_devices()
        assert len(devices) == 1
        assert devices[0].id == device.id

    async def test_get_device_detail_foreign_id_raises(self, async_db_session):
        """get_device_detail with a foreign device_id raises NotFoundError."""
        profile_a = await _make_profile(async_db_session)
        profile_b = await _make_profile(async_db_session)
        foreign_device = await _make_device(async_db_session, profile_b.id)

        service = DeviceService(async_db_session, profile_a.id)
        with pytest.raises(NotFoundError):
            await service.get_device_detail(foreign_device.id)

    async def test_get_device_detail_own_id_succeeds(self, async_db_session):
        """get_device_detail returns detail for a device owned by this profile."""
        profile = await _make_profile(async_db_session)
        device = await _make_device(async_db_session, profile.id)

        service = DeviceService(async_db_session, profile.id)
        detail = await service.get_device_detail(device.id)
        assert detail.id == device.id

    async def test_cross_profile_devices_invisible(self, async_db_session):
        """Two profiles with one device each: each service sees only its own."""
        profile_a = await _make_profile(async_db_session)
        profile_b = await _make_profile(async_db_session)
        dev_a = await _make_device(async_db_session, profile_a.id)
        dev_b = await _make_device(async_db_session, profile_b.id)

        result_a = await DeviceService(async_db_session, profile_a.id).list_devices()
        result_b = await DeviceService(async_db_session, profile_b.id).list_devices()

        assert [d.id for d in result_a] == [dev_a.id]
        assert [d.id for d in result_b] == [dev_b.id]


# ---------------------------------------------------------------------------
# SessionService isolation
# ---------------------------------------------------------------------------


class TestSessionServiceIsolation:
    """SessionService respects profile_id boundaries via device ownership."""

    async def test_list_sessions_excludes_foreign_profile(self, async_db_session):
        """list_sessions with profile A returns empty when sessions belong to profile B."""
        profile_a = await _make_profile(async_db_session)
        profile_b = await _make_profile(async_db_session)
        dev_b = await _make_device(async_db_session, profile_b.id)
        await _make_session(async_db_session, dev_b.id)

        service = SessionService(async_db_session, profile_a.id)
        result = await service.list_sessions()
        assert result.sessions == []
        assert result.total_count == 0

    async def test_list_sessions_includes_own_profile(self, async_db_session):
        """list_sessions returns sessions for the profile's own devices."""
        profile = await _make_profile(async_db_session)
        device = await _make_device(async_db_session, profile.id)
        sess = await _make_session(async_db_session, device.id)

        service = SessionService(async_db_session, profile.id)
        result = await service.list_sessions()
        assert result.total_count == 1
        assert result.sessions[0].id == sess.id

    async def test_get_session_detail_foreign_id_raises(self, async_db_session):
        """get_session_detail with a session owned by another profile raises NotFoundError."""
        profile_a = await _make_profile(async_db_session)
        profile_b = await _make_profile(async_db_session)
        dev_b = await _make_device(async_db_session, profile_b.id)
        foreign_sess = await _make_session(async_db_session, dev_b.id)

        service = SessionService(async_db_session, profile_a.id)
        with pytest.raises(NotFoundError):
            await service.get_session_detail(foreign_sess.id)

    async def test_two_profiles_see_only_own_sessions(self, async_db_session):
        """Sessions stay partitioned: each profile sees exactly its own count."""
        profile_a = await _make_profile(async_db_session)
        profile_b = await _make_profile(async_db_session)
        dev_a = await _make_device(async_db_session, profile_a.id)
        dev_b = await _make_device(async_db_session, profile_b.id)
        sess_a = await _make_session(async_db_session, dev_a.id)
        await _make_session(async_db_session, dev_b.id)

        result_a = await SessionService(async_db_session, profile_a.id).list_sessions()
        result_b = await SessionService(async_db_session, profile_b.id).list_sessions()

        assert result_a.total_count == 1
        assert result_a.sessions[0].id == sess_a.id
        assert result_b.total_count == 1
