"""Tests for imported_session_ids thread-through (PR-A, §step 6).

Covers:
1. import_sessions_batch returns the correct DB Session.id values on success.
2. import_sessions_batch excludes IDs for skipped (already-exists) sessions.
3. ImportResult.imported_session_ids is populated from per-source batches.
4. ImportResult with no imports has empty imported_session_ids.
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime

import pytest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from snore.database import models
from snore.database.importers import SessionImporter
from snore.database.models import Base
from snore.parsers.unified import DeviceInfo, UnifiedSession
from snore.services.schemas import ImportResult, ImportSource, ImportSourceResult

# ---------------------------------------------------------------------------
# Fixtures
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


def _session(serial: str, device_session_id: str | None = None) -> UnifiedSession:
    """Minimal UnifiedSession stub (no waveforms / events / statistics)."""
    return UnifiedSession(
        device_info=DeviceInfo(
            manufacturer="TestMfg",
            model="TestModel",
            serial_number=serial,
        ),
        device_session_id=device_session_id or f"sess_{uuid.uuid4().hex[:8]}",
        start_time=datetime(2025, 1, 1, 22, 0, 0, tzinfo=UTC),
        end_time=datetime(2025, 1, 2, 6, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Test 1: fresh imports return one positive integer ID per session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_sessions_batch_returns_correct_ids_for_new_sessions(mem_db):
    """import_sessions_batch returns a distinct DB Session.id per imported row."""
    db, profile_id = mem_db
    serial = f"SN_{uuid.uuid4().hex[:8]}"
    sessions = [_session(serial), _session(serial)]

    importer = SessionImporter(profile_id=profile_id)
    imported, skipped, failed, session_ids = await importer.import_sessions_batch(
        sessions, db=db
    )
    await db.commit()

    assert imported == 2
    assert skipped == 0
    assert failed == 0
    assert len(session_ids) == 2
    assert len(set(session_ids)) == 2, "IDs must be distinct"
    assert all(isinstance(sid, int) and sid > 0 for sid in session_ids)


# ---------------------------------------------------------------------------
# Test 2: skipped sessions do NOT appear in the returned IDs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_sessions_batch_excludes_ids_for_skipped_sessions(mem_db):
    """Importing an already-imported session yields empty session_ids (skip path)."""
    db, profile_id = mem_db
    serial = f"SN_{uuid.uuid4().hex[:8]}"
    device_session_id = f"sess_{uuid.uuid4().hex[:8]}"

    # First import — new row, ID returned.
    importer = SessionImporter(profile_id=profile_id)
    imported1, skipped1, failed1, ids1 = await importer.import_sessions_batch(
        [_session(serial, device_session_id)], db=db
    )
    await db.commit()
    assert imported1 == 1 and len(ids1) == 1

    # Second import of the same session — skipped, no ID.
    imported2, skipped2, failed2, ids2 = await importer.import_sessions_batch(
        [_session(serial, device_session_id)], db=db
    )
    await db.commit()

    assert imported2 == 0
    assert skipped2 == 1
    assert ids2 == []


# ---------------------------------------------------------------------------
# Test 3: ImportResult.imported_session_ids aggregates source-level IDs
# ---------------------------------------------------------------------------


def test_import_result_imported_session_ids_aggregates_per_source_ids():
    """ImportResult.imported_session_ids carries the union of per-source ID lists."""
    _source = ImportSource(parser_name="resmed", root_path="/sd")
    source_a = ImportSourceResult(
        source=_source, imported=1, imported_session_ids=[101]
    )
    source_b = ImportSourceResult(
        source=_source, imported=2, imported_session_ids=[202, 203]
    )

    result = ImportResult(
        total_imported=3,
        sources=[source_a, source_b],
        imported_session_ids=[101, 202, 203],
    )

    assert result.imported_session_ids == [101, 202, 203]
    assert source_a.imported_session_ids == [101]
    assert source_b.imported_session_ids == [202, 203]


# ---------------------------------------------------------------------------
# Test 4: empty import → empty session_ids
# ---------------------------------------------------------------------------


def test_import_result_empty_when_nothing_imported():
    """ImportResult defaults to an empty imported_session_ids list."""
    result = ImportResult(total_imported=0, total_skipped=5)
    assert result.imported_session_ids == []
