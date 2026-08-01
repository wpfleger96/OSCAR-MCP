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

    @pytest.fixture
    def sqlite_dialect(self):
        """Return a SQLite dialect instance for bind-param tests."""
        from sqlalchemy.dialects import sqlite as _sqlite

        return _sqlite.dialect()

    @pytest.fixture
    def pg_dialect(self):
        """Return a PostgreSQL dialect instance for bind-param tests."""
        from sqlalchemy.dialects import postgresql as _pg

        return _pg.dialect()  # type: ignore[no-untyped-call]

    def test_bind_utc_aware_sqlite_returns_naive_utc(self, sqlite_dialect):
        """UTC-aware datetime on SQLite is stored as naive UTC (no TZ in string)."""
        udt = UTCDateTime()
        dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        result = udt.process_bind_param(dt, dialect=sqlite_dialect)
        assert result is not None
        assert result.tzinfo is None
        assert result.hour == 12

    def test_bind_utc_aware_postgresql_returns_aware_utc(self, pg_dialect):
        """UTC-aware datetime on PostgreSQL is passed as an aware UTC datetime."""
        udt = UTCDateTime()
        dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        result = udt.process_bind_param(dt, dialect=pg_dialect)
        assert result is not None
        assert result.tzinfo is not None, (
            "PostgreSQL bind must pass an aware datetime to the driver"
        )
        assert result.utcoffset() == timedelta(0)
        assert result.hour == 12

    def test_bind_non_utc_offset_is_normalised_to_utc(self, sqlite_dialect):
        """Aware datetime with non-UTC offset is normalised: +05:30 → UTC −5h30m."""
        udt = UTCDateTime()
        tz_530 = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2025, 6, 15, 17, 30, 0, tzinfo=tz_530)  # 12:00 UTC
        result = udt.process_bind_param(dt, dialect=sqlite_dialect)
        assert result is not None
        assert result.tzinfo is None
        assert result.hour == 12
        assert result.minute == 0

    def test_bind_minus_8_offset_is_normalised_to_utc(self, sqlite_dialect):
        """Aware datetime at UTC-8 is normalised: 20:00 UTC-8 → 04:00 UTC (next day)."""
        udt = UTCDateTime()
        tz_minus8 = timezone(timedelta(hours=-8))
        dt = datetime(2025, 6, 15, 20, 0, 0, tzinfo=tz_minus8)  # 2025-06-16 04:00 UTC
        result = udt.process_bind_param(dt, dialect=sqlite_dialect)
        assert result is not None
        assert result.date() == datetime(2025, 6, 16).date()
        assert result.hour == 4

    def test_bind_naive_datetime_raises_value_error(self, sqlite_dialect):
        """Naive datetime (no tzinfo) is rejected with a clear error."""
        udt = UTCDateTime()
        naive = datetime(2025, 6, 15, 12, 0, 0)
        with pytest.raises(ValueError, match="UTCDateTime requires tz-aware"):
            udt.process_bind_param(naive, dialect=sqlite_dialect)

    def test_bind_none_returns_none(self, sqlite_dialect):
        """None passthrough — nullable column semantics."""
        udt = UTCDateTime()
        assert udt.process_bind_param(None, dialect=sqlite_dialect) is None


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


# ---------------------------------------------------------------------------
# Dialect-aware impl tests
# ---------------------------------------------------------------------------


class TestUTCDateTimeDialectAware:
    """UTCDateTime.load_dialect_impl returns dialect-appropriate impl."""

    def test_sqlite_dialect_returns_non_tz_datetime(self):
        """On SQLite, UTCDateTime uses DateTime(timezone=False)."""
        from sqlalchemy.dialects import sqlite as _sqlite

        udt = UTCDateTime()
        dialect = _sqlite.dialect()
        impl = udt.load_dialect_impl(dialect)
        # SQLite impl should be a DateTime without timezone.
        assert impl is not None
        # The impl type should be a non-tz DateTime (not TIMESTAMPTZ).
        assert hasattr(impl, "timezone")
        assert not impl.timezone

    def test_postgresql_dialect_returns_tz_datetime(self):
        """On PostgreSQL, UTCDateTime uses DateTime(timezone=True)."""
        from sqlalchemy.dialects import postgresql as _pg

        udt = UTCDateTime()
        dialect = _pg.dialect()  # type: ignore[no-untyped-call]
        impl = udt.load_dialect_impl(dialect)
        assert impl is not None
        assert hasattr(impl, "timezone")
        assert impl.timezone is True

    def test_sqlite_dialect_compiles_without_timezone_suffix(self):
        """UTCDateTime compiles to DATETIME (no timezone) on SQLite."""
        from sqlalchemy import Column, MetaData, Table
        from sqlalchemy.dialects import sqlite as _sqlite

        meta = MetaData()
        t = Table("_test", meta, Column("ts", UTCDateTime()))
        dialect = _sqlite.dialect()
        compiled = str(t.c.ts.type.compile(dialect=dialect))
        # Should not include TIMEZONE or TIMESTAMPTZ.
        assert "TIMEZONE" not in compiled.upper()

    def test_postgresql_dialect_compiles_with_timezone_suffix(self):
        """UTCDateTime compiles to TIMESTAMP WITH TIME ZONE on PostgreSQL."""
        from sqlalchemy import Column, MetaData, Table
        from sqlalchemy.dialects import postgresql as _pg

        meta = MetaData()
        t = Table("_test", meta, Column("ts", UTCDateTime()))
        dialect = _pg.dialect()  # type: ignore[no-untyped-call]
        compiled = str(t.c.ts.type.compile(dialect=dialect))
        # PostgreSQL should get TIMESTAMP WITH TIME ZONE.
        assert "TIME ZONE" in compiled.upper() or "TIMESTAMPTZ" in compiled.upper()


# ---------------------------------------------------------------------------
# AnalysisResult.created_at ordering via real model path (§3)
# ---------------------------------------------------------------------------


class TestAnalysisResultOrdering:
    """Latest-result selection uses created_at (UTCDateTime) with correct ordering."""

    def test_latest_analysis_result_selected_by_created_at(self, tmp_path):
        """When multiple AnalysisResult rows exist, the one with the latest
        created_at is selected correctly — even when inserted in reversed order.
        """
        from datetime import UTC

        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session as SASession

        from snore.database.models import (
            AnalysisResult,
            Base,
            Day,
            Device,
        )
        from snore.database.models import (
            Session as DBSession,
        )

        db_path = str(tmp_path / "analysis_ordering.db")
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        earlier = datetime(2024, 6, 1, 10, 0, 0, tzinfo=UTC)
        start = datetime(2024, 6, 1, 21, 0, 0)
        end = datetime(2024, 6, 2, 5, 0, 0)  # 8h session

        with SASession(engine) as db:
            device = Device(
                manufacturer="TestMfr", model="M1", serial_number="SN_ORDERING"
            )
            db.add(device)
            db.flush()

            day = Day(device_id=device.id, date=start.date())
            db.add(day)
            db.flush()

            session = DBSession(
                device_id=device.id,
                day_id=day.id,
                device_session_id="SESS_ORDER",
                start_time=start,
                end_time=end,
            )
            db.add(session)
            db.flush()

            # Insert the OLDER result first, then the NEWER one — to verify
            # ordering is by created_at, not insertion order.
            older_result = AnalysisResult(
                session_id=session.id,
                created_at=earlier,
                timestamp_start=start,
                timestamp_end=end,
            )
            newer_result = AnalysisResult(
                session_id=session.id,
                created_at=now,
                timestamp_start=start,
                timestamp_end=end,
            )
            db.add_all([older_result, newer_result])
            db.flush()

            # Verify that querying DESC by created_at returns the newer result first.
            stmt = (
                select(AnalysisResult)
                .where(AnalysisResult.session_id == session.id)
                .order_by(AnalysisResult.created_at.desc())
                .limit(1)
            )
            latest = db.execute(stmt).scalars().first()

            assert latest is not None
            assert latest.created_at is not None
            # The latest result has the newer timestamp.
            assert latest.created_at == now, (
                f"Expected created_at={now}, got {latest.created_at}"
            )
            # Verify UTC tzinfo is attached.
            assert latest.created_at.tzinfo is not None, (
                "UTCDateTime must restore tzinfo=UTC on load"
            )
            assert latest.created_at.utcoffset() == timedelta(0)

        engine.dispose()

    def test_latest_result_via_production_path_with_mixed_offsets(self, tmp_path):
        """``AnalysisService.get_analysis_result()`` returns the newest by UTC order.

        Supplies one result timestamped with UTC+2 (earlier in absolute UTC)
        and one with UTC-8 (later in absolute UTC), stored in reversed order.
        The production query path (``AnalysisService.get_analysis_result``) must
        return the row whose absolute UTC instant is later, regardless of which
        timezone offset the datetime carried when stored.
        """
        from datetime import UTC, timedelta, timezone

        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session as SASession

        from snore.analysis.service import AnalysisService
        from snore.database.models import AnalysisResult, Base, Day, Device
        from snore.database.models import Session as DBSession

        db_path = str(tmp_path / "mixed_offset_ordering.db")
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)

        tz_plus2 = timezone(timedelta(hours=2))
        tz_minus8 = timezone(timedelta(hours=-8))

        # "earlier" stored as 14:00+02:00 == 12:00 UTC
        # "later" stored as 06:00-08:00 == 14:00 UTC
        # later_wall_hour < earlier_wall_hour, so naïve comparison would invert the order.
        earlier_utc_plus2 = __import__("datetime").datetime(
            2025, 3, 15, 14, 0, 0, tzinfo=tz_plus2
        )
        later_utc_minus8 = __import__("datetime").datetime(
            2025, 3, 15, 6, 0, 0, tzinfo=tz_minus8
        )

        # Sanity check: later > earlier in absolute UTC.
        assert later_utc_minus8.astimezone(UTC) > earlier_utc_plus2.astimezone(UTC)

        start = __import__("datetime").datetime(2024, 1, 1, 21, 0, 0)
        end = __import__("datetime").datetime(2024, 1, 2, 5, 0, 0)

        # Minimal AnalysisResult JSON with distinguishing duration_hours values.
        def _make_result_json(session_id: int, duration_hours: float) -> dict:
            return {
                "session_id": session_id,
                "session_duration_hours": duration_hours,
                "total_breaths": 0,
                "machine_events": [],
                "mode_results": {},
                "timestamp_start": 0.0,
                "timestamp_end": 0.0,
            }

        with SASession(engine) as db:
            device = Device(manufacturer="Mfr", model="M", serial_number="SN_MIXED")
            db.add(device)
            db.flush()
            day = Day(device_id=device.id, date=start.date())
            db.add(day)
            db.flush()
            session = DBSession(
                device_id=device.id,
                day_id=day.id,
                device_session_id="SESS_MIXED",
                start_time=start,
                end_time=end,
            )
            db.add(session)
            db.flush()

            # Insert the "earlier" result first (larger wall-clock hour).
            # We distinguish the two rows by session_duration_hours in the JSON.
            older = AnalysisResult(
                session_id=session.id,
                created_at=earlier_utc_plus2,
                timestamp_start=start,
                timestamp_end=end,
                programmatic_result_json=_make_result_json(session.id, 6.0),
            )
            # Insert the "later" result second (smaller wall-clock hour).
            newer = AnalysisResult(
                session_id=session.id,
                created_at=later_utc_minus8,
                timestamp_start=start,
                timestamp_end=end,
                programmatic_result_json=_make_result_json(session.id, 8.0),
            )
            db.add_all([older, newer])
            db.commit()

            # Call the PRODUCTION PATH: AnalysisService.get_analysis_result().
            # This exercises the same ordering clause the real app uses.
            svc = AnalysisService(db)
            result = svc.get_analysis_result(session.id)

        assert result is not None, "Production path must return a result"
        # The method must return the row with the later absolute UTC instant.
        # That row has duration_hours=8.0 (the "later" one, stored as UTC-8).
        assert result.session_duration_hours == 8.0, (
            f"Expected the later UTC result (duration=8.0), "
            f"got duration={result.session_duration_hours} — "
            "mixed-offset ordering is wrong in the production path"
        )

        engine.dispose()
