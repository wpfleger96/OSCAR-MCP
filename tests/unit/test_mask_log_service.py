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


class TestCreateEntryAllOptional:
    """Tests for create_entry() with all identity fields now optional."""

    async def test_all_none_create_entry_round_trips(
        self, async_db_session, async_test_profile
    ):
        """Creating an entry with all identity fields None persists and returns them as None."""
        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.create_entry(
            brand=None,
            model=None,
            style=None,
            start_date=None,
        )

        assert result.id is not None
        assert result.brand is None
        assert result.model is None
        assert result.style is None
        assert result.start_date is None
        assert result.size is None
        assert result.notes is None

    async def test_partial_create_brand_only_round_trips(
        self, async_db_session, async_test_profile
    ):
        """Creating an entry with only brand set returns brand and nulls for the rest."""
        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.create_entry(
            brand="ResMed",
            model=None,
            style=None,
            start_date=None,
        )

        assert result.brand == "ResMed"
        assert result.model is None
        assert result.style is None
        assert result.start_date is None


class TestGetActiveEntryNullDate:
    """Tests that null-start_date entries are never considered active for any date."""

    async def test_null_start_date_entry_never_returned_as_active(
        self, async_db_session, async_test_profile
    ):
        """An entry with start_date=None is never returned by get_active_entry_for_date."""
        entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=None,
        )
        async_db_session.add(entry)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))

        assert result is None

    async def test_null_date_entry_invisible_even_as_only_entry(
        self, async_db_session, async_test_profile
    ):
        """Null-date entry as the only entry: any target_date returns None."""
        entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand=None,
            model=None,
            style=None,
            start_date=None,
        )
        async_db_session.add(entry)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        # Query both the distant past and far future — null-date entry must not appear.
        assert await service.get_active_entry_for_date(date(2000, 1, 1)) is None
        assert await service.get_active_entry_for_date(date(2099, 12, 31)) is None

    async def test_null_date_entry_does_not_shadow_earlier_dated_entry(
        self, async_db_session, async_test_profile
    ):
        """With a null-date entry and a dated entry, the dated entry is still returned."""
        dated_entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=date(2025, 6, 1),
        )
        null_entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="Philips",
            model="DreamWear",
            style="nasal",
            start_date=None,
        )
        async_db_session.add(dated_entry)
        async_db_session.add(null_entry)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        result = await service.get_active_entry_for_date(date(2025, 6, 15))

        assert result is not None
        assert result.brand == "ResMed"
        assert result.model == "AirFit P10"


class TestListEntriesOrdering:
    """Tests for list_entries() ORDER BY start_date NULLS LAST ordering."""

    async def test_null_start_date_entries_ordered_last(
        self, async_db_session, async_test_profile
    ):
        """Entries with null start_date appear after all dated entries."""
        dated_early = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=date(2025, 3, 1),
        )
        dated_late = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="Philips",
            model="DreamWear",
            style="nasal",
            start_date=date(2025, 6, 1),
        )
        null_entry = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="Fisher & Paykel",
            model="Evora",
            style="full_face",
            start_date=None,
        )
        # Insert in non-date order to confirm ordering comes from SQL, not insert order.
        async_db_session.add(null_entry)
        async_db_session.add(dated_late)
        async_db_session.add(dated_early)
        await async_db_session.flush()

        service = MaskLogService(async_db_session, async_test_profile.id)
        results = await service.list_entries()

        assert len(results) == 3
        assert results[0].start_date == date(2025, 3, 1)
        assert results[1].start_date == date(2025, 6, 1)
        assert results[2].start_date is None

    async def test_multiple_null_start_date_entries_ordered_by_id(
        self, async_db_session, async_test_profile
    ):
        """Multiple null-date entries are ordered by id as the tiebreak."""
        first_null = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=None,
        )
        async_db_session.add(first_null)
        await async_db_session.flush()

        second_null = MaskLogEntry(
            profile_id=async_test_profile.id,
            brand="Philips",
            model="DreamWear",
            style="nasal",
            start_date=None,
        )
        async_db_session.add(second_null)
        await async_db_session.flush()

        assert second_null.id > first_null.id

        service = MaskLogService(async_db_session, async_test_profile.id)
        results = await service.list_entries()

        assert len(results) == 2
        assert results[0].id == first_null.id
        assert results[1].id == second_null.id
