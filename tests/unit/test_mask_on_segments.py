"""Tests for mask-on segment persistence (validity flags v1).

Covers:
1. ResMed night merge records one [start, end) interval per EDF segment in
   merged-session offset seconds.
2. A single-segment night stores [(0.0, duration)] — "known, no gaps" stays
   distinguishable from None = unknown.
3. Importer round-trip: UnifiedSession.mask_on_segments lands in the
   sessions.mask_on_segments JSON column (tuples serialized to lists).
4. Sessions without segment info (e.g. OSCAR imports) persist NULL.
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from snore.database import models
from snore.database.importers import SessionImporter
from snore.database.models import Base
from snore.parsers.resmed_edf import ResmedEDFParser
from snore.parsers.unified import DeviceInfo, UnifiedSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _device_info() -> DeviceInfo:
    return DeviceInfo(
        manufacturer="ResMed",
        model="AirSense 10",
        serial_number=f"SN_{uuid.uuid4().hex[:8]}",
    )


def _segment_session(start: datetime, duration_s: float) -> UnifiedSession:
    """Minimal per-segment UnifiedSession as _parse_session_group would build."""
    return UnifiedSession(
        device_info=_device_info(),
        device_session_id=start.strftime("%Y%m%d_%H%M%S"),
        start_time=start,
        end_time=start + timedelta(seconds=duration_s),
    )


def _parse_night(
    segment_sessions: list[UnifiedSession], monkeypatch: pytest.MonkeyPatch
) -> UnifiedSession:
    """Drive _parse_night_session with synthetic pre-parsed segment sessions."""
    parser = ResmedEDFParser()
    queue = list(segment_sessions)
    monkeypatch.setattr(
        parser,
        "_parse_session_group",
        lambda *args, **kwargs: queue.pop(0),
    )
    segments = {
        s.device_session_id: {"BRP": Path(f"/nonexistent/{s.device_session_id}.edf")}
        for s in segment_sessions
    }
    night = parser._parse_night_session(
        night_date="20250910",
        segments=segments,
        device_info=_device_info(),
        base_path=Path("/nonexistent"),
    )
    assert night is not None
    return night


# ---------------------------------------------------------------------------
# Parser: segment recording
# ---------------------------------------------------------------------------


class TestParserMaskOnSegments:
    def test_multi_segment_merge_records_one_interval_per_segment(self, monkeypatch):
        """Each EDF segment yields one [start, end) interval at its merged offset."""
        base = datetime(2025, 9, 10, 22, 0, 0)
        night = _parse_night(
            [
                _segment_session(base, 3600.0),  # 22:00–23:00
                # 10-min mask-off gap, then 23:10–01:10 (7200 s)
                _segment_session(base + timedelta(seconds=4200), 7200.0),
            ],
            monkeypatch,
        )

        assert night.mask_on_segments == [(0.0, 3600.0), (4200.0, 11400.0)]
        # Prose data-quality notes stay unchanged.
        assert any("2 segment(s)" in note for note in night.data_quality_notes)

    def test_three_segments_record_three_ascending_intervals(self, monkeypatch):
        """Intervals stay ascending and per-segment across multiple gaps."""
        base = datetime(2025, 9, 10, 22, 0, 0)
        night = _parse_night(
            [
                _segment_session(base, 1800.0),
                _segment_session(base + timedelta(seconds=2400), 1800.0),
                _segment_session(base + timedelta(seconds=4800), 1800.0),
            ],
            monkeypatch,
        )

        assert night.mask_on_segments == [
            (0.0, 1800.0),
            (2400.0, 4200.0),
            (4800.0, 6600.0),
        ]

    def test_single_segment_records_full_duration_interval(self, monkeypatch):
        """A single-segment night stores [(0.0, duration)], not None."""
        base = datetime(2025, 9, 10, 22, 0, 0)
        night = _parse_night([_segment_session(base, 3600.0)], monkeypatch)

        assert night.mask_on_segments == [(0.0, 3600.0)]

    def test_unified_session_defaults_to_unknown(self):
        """Sessions built without segment info (e.g. OSCAR) default to None."""
        session = _segment_session(datetime(2025, 9, 10, 22, 0, 0), 3600.0)
        assert session.mask_on_segments is None


# ---------------------------------------------------------------------------
# Importer round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
async def mem_db():
    """In-memory async SQLite session with schema created and a single profile."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    session = factory()

    user = models.User(
        canonical_email=f"user_{uuid.uuid4().hex[:8]}@example.com",
        role="admin",
    )
    session.add(user)
    await session.flush()
    profile = models.Profile(user_id=user.id, name="Default")
    session.add(profile)
    await session.flush()
    await session.commit()

    yield session, profile.id

    await session.close()
    await engine.dispose()


class TestImporterMaskOnSegments:
    async def test_segments_round_trip_to_json_column(self, mem_db):
        """UnifiedSession.mask_on_segments lands in the DB as list-of-lists."""
        db, profile_id = mem_db
        unified = UnifiedSession(
            device_info=_device_info(),
            device_session_id=f"sess_{uuid.uuid4().hex[:8]}",
            start_time=datetime(2025, 9, 10, 22, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 9, 11, 1, 10, 0, tzinfo=UTC),
            mask_on_segments=[(0.0, 3600.0), (4200.0, 11400.0)],
        )

        importer = SessionImporter(profile_id=profile_id)
        imported, _, _, session_ids = await importer.import_sessions_batch(
            [unified], db=db
        )
        await db.commit()
        assert imported == 1

        row = (
            await db.execute(
                select(models.Session).where(models.Session.id == session_ids[0])
            )
        ).scalar_one()
        assert row.mask_on_segments == [[0.0, 3600.0], [4200.0, 11400.0]]

    async def test_unknown_segments_persist_null(self, mem_db):
        """mask_on_segments=None (unknown) stays NULL in the DB."""
        db, profile_id = mem_db
        unified = UnifiedSession(
            device_info=_device_info(),
            device_session_id=f"sess_{uuid.uuid4().hex[:8]}",
            start_time=datetime(2025, 9, 10, 22, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 9, 11, 6, 0, 0, tzinfo=UTC),
        )

        importer = SessionImporter(profile_id=profile_id)
        imported, _, _, session_ids = await importer.import_sessions_batch(
            [unified], db=db
        )
        await db.commit()
        assert imported == 1

        row = (
            await db.execute(
                select(models.Session).where(models.Session.id == session_ids[0])
            )
        ).scalar_one()
        assert row.mask_on_segments is None
