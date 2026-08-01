"""Pytest configuration and fixtures for SNORE tests."""

import sqlite3
import tempfile  # noqa: F401 — kept for backwards compat with any external imports

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_converter("DATETIME", lambda s: datetime.fromisoformat(s.decode()))


def pytest_configure(config):
    """Register custom test markers."""
    config.addinivalue_line(
        "markers", "unit: Unit tests that do not require external dependencies"
    )
    config.addinivalue_line("markers", "parser: Tests for device parsers")
    config.addinivalue_line(
        "markers", "integration: Integration tests combining multiple components"
    )
    config.addinivalue_line(
        "markers", "integration_pipeline: Full end-to-end pipeline integration tests"
    )
    config.addinivalue_line(
        "markers", "integration_features: Feature extraction integration tests"
    )
    config.addinivalue_line(
        "markers", "real_data: Tests that process actual CPAP session data"
    )
    config.addinivalue_line(
        "markers", "recorded: Tests using recorded PAP session data from device"
    )
    config.addinivalue_line(
        "markers", "requires_fixtures: Tests that require real session fixtures"
    )
    config.addinivalue_line(
        "markers", "slow: Tests that take significant time (>5 seconds)"
    )


def pytest_collection_modifyitems(items):
    """Auto-apply unit/integration markers based on test location."""
    for item in items:
        path = str(item.fspath)
        if "/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/integration/" in path:
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def resmed_fixture_path(fixtures_dir):
    """Return path to ResMed test data."""
    return fixtures_dir / "device_data" / "resmed"


@pytest.fixture
def resmed_parser():
    """Return a ResMed EDF parser instance."""
    from snore.parsers.resmed_edf import ResmedEDFParser

    return ResmedEDFParser()


@pytest.fixture
def parser_registry():
    """Return the global parser registry with parsers registered."""
    from snore.parsers.register_all import register_all_parsers
    from snore.parsers.registry import parser_registry

    register_all_parsers()

    return parser_registry


# =============================================================================
# Database Test Fixtures
# =============================================================================


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for testing with a collision-proof name.

    Uses ``tmp_path`` (pytest's per-test isolated directory) plus a UUID so
    parallel xdist workers never share or collide on the same file — even when
    their clocks have the same millisecond timestamp.
    """
    import uuid

    db_path = tmp_path / f"test_snore_{uuid.uuid4().hex}.db"

    yield db_path

    if db_path.exists():
        db_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        wal_file = Path(str(db_path) + ext)
        if wal_file.exists():
            wal_file.unlink(missing_ok=True)


@pytest.fixture
def db_session(temp_db):
    """Create fresh database session for each test with proper isolation."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from snore.database.models import Base

    engine = create_engine(f"sqlite:///{temp_db}")

    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
async def async_db_session(temp_db):
    """Create fresh async database session for each test.

    Used by tests for services that have been converted to AsyncSession in PR-2.
    The underlying database is the same temporary SQLite file as ``db_session``.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from snore.database.models import Base

    async_url = f"sqlite+aiosqlite:///{temp_db}"
    engine = create_async_engine(async_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    session = factory()

    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.fixture
def test_device(db_session):
    """Create a test device."""
    import uuid

    from snore.database.models import Device

    device = Device(
        manufacturer="Test Manufacturer",
        model="Test Model",
        serial_number=f"TEST_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(device)
    db_session.flush()
    return device


@pytest.fixture
async def async_test_device(async_db_session):
    """Create a test device using the async session fixture."""
    import uuid

    from snore.database.models import Device

    device = Device(
        manufacturer="Test Manufacturer",
        model="Test Model",
        serial_number=f"TEST_{uuid.uuid4().hex[:8]}",
    )
    async_db_session.add(device)
    await async_db_session.flush()
    return device


@pytest.fixture
def test_session_factory(db_session):
    """Factory for creating test sessions with statistics."""
    import uuid

    from snore.database.models import Session, Statistics

    def _create_session(device_id, start_time, duration_hours=8.0, **stats_kwargs):
        session = Session(
            device_id=device_id,
            device_session_id=f"test_{start_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}",
            start_time=start_time,
            end_time=start_time + timedelta(hours=duration_hours),
            duration_seconds=duration_hours * 3600,
            has_statistics=bool(stats_kwargs),
        )
        db_session.add(session)
        db_session.flush()

        if stats_kwargs:
            stats = Statistics(session_id=session.id, **stats_kwargs)
            db_session.add(stats)
            db_session.flush()
            db_session.refresh(session)

        return session

    return _create_session


@pytest.fixture
def async_test_session_factory(async_db_session):
    """Async factory for creating test sessions with statistics.

    Returns a coroutine factory — callers must await each call:
        session = await async_test_session_factory(device_id, start_time)
    """
    import uuid

    from snore.database.models import Session, Statistics

    async def _create_session(device_id, start_time, duration_hours=8.0, **stats_kwargs):
        session = Session(
            device_id=device_id,
            device_session_id=f"test_{start_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}",
            start_time=start_time,
            end_time=start_time + timedelta(hours=duration_hours),
            duration_seconds=duration_hours * 3600,
            has_statistics=bool(stats_kwargs),
        )
        async_db_session.add(session)
        await async_db_session.flush()

        if stats_kwargs:
            stats = Statistics(session_id=session.id, **stats_kwargs)
            async_db_session.add(stats)
            await async_db_session.flush()
            await async_db_session.refresh(session)

        return session

    return _create_session


# =============================================================================
# Integration Test Fixtures
# =============================================================================


@pytest.fixture
def recorded_session(db_session):
    """Factory for loading recorded session fixtures by YYYYMMDD ID.

    Usage:
        def test_something(self, recorded_session):
            db = recorded_session("20250808")
            session = db.query(Session).first()

    Available sessions:
        - 20250110: Early therapy session (January 2025)
        - 20250808: Baseline session (August 2025)
        - 20250910: Multi-segment session (September 2025, 4 therapy segments)
        - 20251025: Event detection test session (October 2025)
    """
    from tests.helpers.fixtures_loader import import_to_test_db

    def _load(session_id: str) -> Any:
        try:
            import_to_test_db(session_id, db_session)
            return db_session
        except (ValueError, FileNotFoundError) as e:
            pytest.skip(f"Fixture {session_id} not available: {e}")

    return _load
