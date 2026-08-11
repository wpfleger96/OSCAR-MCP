"""Unit tests for MaskLogService."""

from datetime import date

from snore.database.models import MaskLogEntry
from snore.services.mask_log_service import MaskLogService


class TestGetActiveEntryForDate:
    """Tests for MaskLogService.get_active_entry_for_date()."""

    async def test_no_entries_returns_none(self, async_db_session, async_test_profile):
        """No entries in the log returns None."""
        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))
        assert result is None

    async def test_single_entry_before_target_is_returned(
        self, async_db_session, async_test_profile
    ):
        """A single entry with start_date before target_date is returned."""
        entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=date(2025, 6, 1),
        )
        async_db_session.add(entry)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))

        assert result is not None
        assert result.brand == "ResMed"
        assert result.model == "AirFit P10"

    async def test_entry_after_target_returns_none(
        self, async_db_session, async_test_profile
    ):
        """An entry with start_date after target_date is not active."""
        entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=date(2025, 7, 1),
        )
        async_db_session.add(entry)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))

        assert result is None

    async def test_multiple_entries_most_recent_wins(
        self, async_db_session, async_test_profile
    ):
        """With multiple entries, the most recent start_date <= target wins."""
        older = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="Philips",
            model="DreamWear",
            style="nasal",
            start_date=date(2025, 3, 1),
        )
        newer = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit N30i",
            style="nasal",
            start_date=date(2025, 5, 15),
        )
        future = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="Fisher & Paykel",
            model="Evora",
            style="nasal",
            start_date=date(2025, 8, 1),
        )
        async_db_session.add(older)
        async_db_session.add(newer)
        async_db_session.add(future)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 1))

        assert result is not None
        assert result.brand == "ResMed"
        assert result.model == "AirFit N30i"

    async def test_same_start_date_higher_id_wins(
        self, async_db_session, async_test_profile
    ):
        """Two entries on the same start_date: the higher id wins."""
        first = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=date(2025, 6, 1),
        )
        async_db_session.add(first)
        await async_db_session.flush()

        second = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit F30i",
            style="full_face",
            start_date=date(2025, 6, 1),
        )
        async_db_session.add(second)
        await async_db_session.flush()

        assert second.id > first.id

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))

        assert result is not None
        assert result.model == "AirFit F30i"

    async def test_different_profile_entry_not_returned(
        self, async_db_session, async_test_profile
    ):
        """An entry belonging to a different profile is not returned."""
        import uuid  # noqa: PLC0415

        from snore.database.models import Profile, User  # noqa: PLC0415

        other_user = User(
            canonical_email=f"other_{uuid.uuid4().hex[:8]}@example.com",
            role="member",
        )
        async_db_session.add(other_user)
        await async_db_session.flush()

        other_profile = Profile(user_id=other_user.id, name="Other Profile")
        async_db_session.add(other_profile)
        await async_db_session.flush()

        foreign_entry = MaskLogEntry(
            profile_id=other_profile.id,
            brand="Philips",
            model="DreamWear",
            style="nasal",
            start_date=date(2025, 1, 1),
        )
        async_db_session.add(foreign_entry)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))

        assert result is None
