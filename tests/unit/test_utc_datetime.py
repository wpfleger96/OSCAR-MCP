"""Tests for UTCDateTime TypeDecorator (§3).

Covers:
- Round-trip: stored value returns with utcoffset() == timedelta(0).
- Non-UTC offset bind: aware non-UTC input is normalised to UTC on the way in.
- Naive bind rejection: UTCDateTime rejects naive datetimes.
- Latest-result ordering: rows with different UTC offsets sort by true UTC order.
- Date-boundary case: midnight UTC survives round-trip.
- SQLite tz restoration: stored value (no tzinfo from SQLite driver) gets UTC attached.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from snore.database.types import UTC_ZERO, UTCDateTime


class TestUTCDateTimeBindParam:
    """process_bind_param: normalise aware datetimes to UTC; reject naive."""

    def test_bind_utc_aware_returns_naive_utc(self):
        """UTC-aware datetime is stored as naive UTC (SQLite has no TZ storage)."""
        udt = UTCDateTime()
        dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        result = udt.process_bind_param(dt, dialect=None)
        assert result is not None
        assert result.tzinfo is None
        assert result.hour == 12

    def test_bind_non_utc_offset_is_normalised_to_utc(self):
        """Aware datetime with non-UTC offset is normalised: +05:30 → UTC −5h30m."""
        udt = UTCDateTime()
        tz_530 = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2025, 6, 15, 17, 30, 0, tzinfo=tz_530)  # 12:00 UTC
        result = udt.process_bind_param(dt, dialect=None)
        assert result is not None
        assert result.tzinfo is None
        assert result.hour == 12
        assert result.minute == 0

    def test_bind_minus_8_offset_is_normalised_to_utc(self):
        """Aware datetime at UTC-8 is normalised: 20:00 UTC-8 → 04:00 UTC (next day)."""
        udt = UTCDateTime()
        tz_minus8 = timezone(timedelta(hours=-8))
        dt = datetime(2025, 6, 15, 20, 0, 0, tzinfo=tz_minus8)  # 2025-06-16 04:00 UTC
        result = udt.process_bind_param(dt, dialect=None)
        assert result is not None
        assert result.date() == datetime(2025, 6, 16).date()
        assert result.hour == 4

    def test_bind_naive_datetime_raises_value_error(self):
        """Naive datetime (no tzinfo) is rejected with a clear error."""
        udt = UTCDateTime()
        naive = datetime(2025, 6, 15, 12, 0, 0)
        with pytest.raises(ValueError, match="UTCDateTime requires tz-aware"):
            udt.process_bind_param(naive, dialect=None)

    def test_bind_none_returns_none(self):
        """None passthrough — nullable column semantics."""
        udt = UTCDateTime()
        assert udt.process_bind_param(None, dialect=None) is None


class TestUTCDateTimeResultValue:
    """process_result_value: attach UTC tzinfo on return."""

    def test_result_naive_datetime_gets_utc_attached(self):
        """Naive datetime from SQLite driver gets UTC tzinfo restored."""
        udt = UTCDateTime()
        naive = datetime(2025, 6, 15, 12, 0, 0)
        result = udt.process_result_value(naive, dialect=None)
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset() == UTC_ZERO

    def test_result_aware_datetime_normalised_to_utc(self):
        """Aware datetime from PostgreSQL driver is normalised to UTC."""
        udt = UTCDateTime()
        tz_5 = timezone(timedelta(hours=5))
        aware = datetime(2025, 6, 15, 17, 0, 0, tzinfo=tz_5)  # 12:00 UTC
        result = udt.process_result_value(aware, dialect=None)
        assert result is not None
        assert result.utcoffset() == UTC_ZERO
        assert result.hour == 12

    def test_result_iso_string_from_sqlite_restored_with_utc(self):
        """ISO string (SQLite edge case) is parsed and UTC is attached."""
        udt = UTCDateTime()
        result = udt.process_result_value("2025-06-15T12:00:00", dialect=None)
        assert result is not None
        assert result.utcoffset() == UTC_ZERO
        assert result.year == 2025

    def test_result_none_returns_none(self):
        """None passthrough."""
        udt = UTCDateTime()
        assert udt.process_result_value(None, dialect=None) is None


class TestUTCDateTimeRoundTrip:
    """End-to-end round-trips through a real SQLAlchemy SQLite column."""

    @pytest.fixture
    def utc_engine_session(self, tmp_path):
        """Return a session connected to a fresh SQLite DB with UTCDateTime."""

        class _Base(DeclarativeBase):
            pass

        class _TS(_Base):
            __tablename__ = "timestamps"
            id: Mapped[int] = mapped_column(
                Integer, primary_key=True, autoincrement=True
            )
            ts: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

        db_path = str(tmp_path / "utc_test.db")
        engine = create_engine(f"sqlite:///{db_path}")
        _Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session, _TS
        engine.dispose()

    def test_round_trip_utc_offset_is_zero(self, utc_engine_session):
        """Stored UTC datetime is restored with utcoffset() == timedelta(0)."""
        session, TS = utc_engine_session
        original = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        row = TS(ts=original)
        session.add(row)
        session.flush()
        session.expire(row)

        loaded = session.get(TS, row.id)
        assert loaded.ts is not None
        assert loaded.ts.utcoffset() == timedelta(0), (
            f"Expected utcoffset timedelta(0), got {loaded.ts.utcoffset()!r}"
        )

    def test_round_trip_non_utc_offset_normalised(self, utc_engine_session):
        """Non-UTC-aware value is normalised to UTC on store and restored correctly."""
        session, TS = utc_engine_session
        tz_plus5 = timezone(timedelta(hours=5))
        original = datetime(2025, 6, 15, 17, 0, 0, tzinfo=tz_plus5)  # 12:00 UTC
        row = TS(ts=original)
        session.add(row)
        session.flush()
        session.expire(row)

        loaded = session.get(TS, row.id)
        assert loaded.ts is not None
        assert loaded.ts.utcoffset() == timedelta(0)
        assert loaded.ts.hour == 12

    def test_midnight_utc_survives_round_trip(self, utc_engine_session):
        """Midnight UTC is not corrupted by the date-boundary."""
        session, TS = utc_engine_session
        original = datetime(2025, 12, 31, 0, 0, 0, tzinfo=UTC)
        row = TS(ts=original)
        session.add(row)
        session.flush()
        session.expire(row)

        loaded = session.get(TS, row.id)
        assert loaded.ts is not None
        assert loaded.ts.hour == 0
        assert loaded.ts.date() == original.date()

    def test_ordering_correct_across_different_offsets(self, utc_engine_session):
        """Rows written with different UTC offsets sort by true UTC chronological order."""
        session, TS = utc_engine_session
        # Row A: 14:00 UTC+2 = 12:00 UTC (earlier)
        # Row B: 13:00 UTC-0 = 13:00 UTC (later)
        tz_plus2 = timezone(timedelta(hours=2))
        earlier = TS(ts=datetime(2025, 6, 15, 14, 0, 0, tzinfo=tz_plus2))
        later = TS(ts=datetime(2025, 6, 15, 13, 0, 0, tzinfo=UTC))
        session.add_all([later, earlier])  # deliberately reversed insertion order
        session.flush()
        session.expire_all()  # force reload through UTCDateTime.process_result_value

        from sqlalchemy import select as sa_select

        rows = session.execute(sa_select(TS).order_by(TS.ts)).scalars().all()

        assert len(rows) == 2
        # The earlier UTC value (12:00 UTC) must come first.
        assert rows[0].ts.hour == 12  # 14:00 UTC+2 normalised to 12:00 UTC
        assert rows[1].ts.hour == 13  # 13:00 UTC

    def test_cache_ok_is_true(self):
        """UTCDateTime.cache_ok must be True so SQLAlchemy can cache statements."""
        udt = UTCDateTime()
        assert udt.cache_ok is True
