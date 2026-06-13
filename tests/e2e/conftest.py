"""Fixtures for the end-to-end suite.

Every test here is auto-marked ``e2e`` (and ``slow``) so the fast unit/
integration suite can exclude them and CI can run them as a dedicated job.

Strategy: do the expensive real import once per session, then hand each test
an isolated *copy* of that database. Copying a closed SQLite file is far
cheaper than re-running ``snore import`` per test, while still giving every
mutating test (analysis, delete, vacuum) a private database.
"""

from __future__ import annotations

import shutil

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e import helpers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RESMED_SD = FIXTURES / "device_data" / "resmed"
RECORDED = FIXTURES / "recorded_sessions"
MULTI_SEGMENT_DAY = "20250910"
# Real recorded nights composed alongside the device night for breadth coverage.
RECORDED_NIGHTS = ["20250110", "20250808", "20250910", "20251025"]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply the ``e2e`` and ``slow`` markers to everything in tests/e2e/."""
    for item in items:
        if f"{Path('tests') / 'e2e'}" in str(item.fspath) or "/e2e/" in str(
            item.fspath
        ):
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session")
def e2e_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Isolated HOME shared by all subprocess invocations (keeps ~/.snore clean)."""
    return tmp_path_factory.mktemp("e2e_home")


@pytest.fixture(scope="session")
def resmed_sd() -> Path:
    """Path to the real ResMed SD-card fixture (single night, full waveforms)."""
    return RESMED_SD


@pytest.fixture(scope="session")
def _base_imported_db(
    tmp_path_factory: pytest.TempPathFactory, e2e_home: Path, resmed_sd: Path
) -> Path:
    """Import the ResMed fixture once; later fixtures copy this file per test."""
    db = tmp_path_factory.mktemp("e2e_base") / "base.db"
    result = helpers.import_fixture(resmed_sd, db, e2e_home)
    assert result.returncode == 0, (
        f"baseline import failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert db.exists(), "baseline import did not create a database"
    return db


def _copy_db(src: Path, dest_dir: Path, name: str = "snore.db") -> Path:
    dest = dest_dir / name
    shutil.copy(src, dest)
    # WAL/SHM are checkpointed when the import process exits, but copy them if
    # present so the database is byte-for-byte equivalent regardless.
    for ext in ("-wal", "-shm"):
        sidecar = Path(str(src) + ext)
        if sidecar.exists():
            shutil.copy(sidecar, Path(str(dest) + ext))
    return dest


@pytest.fixture
def imported_db(_base_imported_db: Path, tmp_path: Path) -> Path:
    """A private, freshly-imported database for a single test (mutation-safe)."""
    return _copy_db(_base_imported_db, tmp_path)


@pytest.fixture(scope="session")
def _base_multi_night_db(
    tmp_path_factory: pytest.TempPathFactory, e2e_home: Path
) -> Path:
    """Import the device night + all recorded nights into one DB, once.

    Yields a 5-session, 5-day dataset spanning 2024-06 → 2025-10 (real data) for
    cross-night tests; later copied per test via ``multi_night_db``.
    """
    missing = [n for n in RECORDED_NIGHTS if not (RECORDED / n).exists()]
    if missing:
        pytest.skip(f"recorded night fixtures missing: {missing}")

    build_dir = tmp_path_factory.mktemp("e2e_multi_build")
    sd = helpers.synthesize_multi_night_sd(
        build_dir,
        identification=RESMED_SD / "Identification.json",
        str_edf=RESMED_SD / "STR.edf",
        night_dirs=[
            RESMED_SD / "DATALOG" / "2024",
            *[RECORDED / n for n in RECORDED_NIGHTS],
        ],
    )
    db = tmp_path_factory.mktemp("e2e_multi") / "multi.db"
    result = helpers.import_fixture(sd, db, e2e_home)
    assert result.returncode == 0, (
        f"multi-night import failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return db


@pytest.fixture
def multi_night_db(_base_multi_night_db: Path, tmp_path: Path) -> Path:
    """A private copy of the multi-night dataset for a single test."""
    return _copy_db(_base_multi_night_db, tmp_path, "multi.db")


@pytest.fixture
def empty_db(tmp_path: Path, e2e_home: Path) -> Path:
    """An initialized but empty database."""
    db = tmp_path / "empty.db"
    result = helpers.run_snore("db", "init", db=db, home=e2e_home)
    assert result.returncode == 0, result.stderr or result.stdout
    return db


@pytest.fixture
def fresh_db_path(tmp_path: Path) -> Path:
    """A path for a database that does not exist yet."""
    return tmp_path / "fresh.db"


@pytest.fixture
def multi_segment_sd(tmp_path: Path) -> Path:
    """Synthesize an importable ResMed SD layout from the multi-segment fixture.

    Skips if the discontinuous recorded session isn't present in the checkout.
    """
    day_dir = RECORDED / MULTI_SEGMENT_DAY
    if not day_dir.exists():
        pytest.skip(f"multi-segment fixture {MULTI_SEGMENT_DAY} not available")
    edf_files = sorted(day_dir.glob("*.edf"))
    if not edf_files:
        pytest.skip(f"no EDF files in {day_dir}")
    return helpers.synthesize_resmed_sd(
        tmp_path,
        identification=RESMED_SD / "Identification.json",
        str_edf=RESMED_SD / "STR.edf",
        edf_files=edf_files,
    )


@pytest.fixture
def snore(e2e_home: Path) -> Callable[..., object]:
    """Convenience runner bound to the isolated HOME.

    Usage: ``snore("session", "list", db=imported_db)``.
    """

    def _run(*args: str, **kwargs: object):  # type: ignore[no-untyped-def]
        return helpers.run_snore(*args, home=e2e_home, **kwargs)

    return _run


@pytest.fixture
def server(e2e_home: Path) -> Callable[[Path], object]:
    """Factory returning a context manager that boots ``snore serve`` on a db."""

    def _start(db: Path):  # type: ignore[no-untyped-def]
        return helpers.live_server(db, e2e_home)

    return _start
