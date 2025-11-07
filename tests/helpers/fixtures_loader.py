"""
Fixture loading utilities for test data.

Provides functions to load real session fixtures and import them to test databases.
"""

from pathlib import Path
from typing import List, Tuple

from sqlalchemy.orm import Session

from oscar_mcp.parsers.resmed_edf import ResmedEDFParser
from oscar_mcp.database.importers import import_session
from oscar_mcp.database.manager import DatabaseManager
from oscar_mcp.database.models import Session as CPAPSession


# Path to fixtures directory
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "real_sessions"


def get_available_fixtures() -> List[str]:
    """
    Get list of available real session fixtures.

    Returns:
        List of fixture names (directory names in real_sessions/)
    """
    if not FIXTURES_DIR.exists():
        return []

    return [d.name for d in FIXTURES_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]


def get_fixture_path(fixture_name: str) -> Path:
    """
    Get path to a specific fixture.

    Args:
        fixture_name: Name of the fixture

    Returns:
        Path to fixture directory

    Raises:
        ValueError: If fixture doesn't exist
    """
    fixture_path = FIXTURES_DIR / fixture_name

    if not fixture_path.exists():
        available = get_available_fixtures()
        raise ValueError(
            f"Fixture '{fixture_name}' not found. Available fixtures: {', '.join(available)}"
        )

    return fixture_path


def get_fixture_files(fixture_name: str) -> dict:
    """
    Get paths to all files in a fixture.

    Args:
        fixture_name: Name of the fixture

    Returns:
        Dict mapping file types to paths (e.g., {"BRP": Path(...), "EVE": Path(...)})
    """
    fixture_path = get_fixture_path(fixture_name)

    files = {}
    for file_path in fixture_path.glob("*.edf"):
        # Extract file type from filename (e.g., "20250215_032456_BRP.edf" -> "BRP")
        parts = file_path.stem.split("_")
        if len(parts) >= 3:
            file_type = parts[2]
            files[file_type] = file_path

    return files


def load_real_session(fixture_name: str) -> Tuple[Path, dict]:
    """
    Load a real session fixture.

    Args:
        fixture_name: Name of the fixture to load

    Returns:
        Tuple of (fixture_directory_path, file_paths_dict)

    Example:
        >>> path, files = load_real_session("2025_baseline")
        >>> print(files.keys())  # ['BRP', 'PLD', 'SA2', 'EVE', 'CSL']
    """
    fixture_path = get_fixture_path(fixture_name)
    files = get_fixture_files(fixture_name)

    return fixture_path, files


def import_to_test_db(
    fixture_name: str,
    db_session: Session,
    profile_name: str = "TestProfile",
    machine_model: str = "AirSense 10",
) -> CPAPSession:
    """
    Import a fixture session to test database.

    Args:
        fixture_name: Name of the fixture to import
        db_session: SQLAlchemy database session
        profile_name: Profile name for the session (not currently used)
        machine_model: Machine model identifier (not currently used)

    Returns:
        Imported CPAPSession object from database

    Note:
        This function bridges between SQLAlchemy test fixtures and the
        DatabaseManager-based import system. It extracts the database path
        from the SQLAlchemy session to create a DatabaseManager.

    Example:
        >>> session = import_to_test_db("2025_baseline", test_db)
        >>> print(session.session_date)
    """
    fixture_path, files = load_real_session(fixture_name)

    # Find BRP file (main data file)
    if "BRP" not in files:
        raise ValueError(f"Fixture {fixture_name} missing BRP file")

    # Extract session_id from BRP filename (e.g., "20250215_032456_BRP.edf" -> "20250215_032456")
    brp_filename = files["BRP"].stem
    session_id = "_".join(brp_filename.split("_")[:2])

    # Create device info (minimal for test fixtures)
    from oscar_mcp.models.unified import DeviceInfo

    device_info = DeviceInfo(
        manufacturer="ResMed",
        model="AirSense 10",
        serial_number="TEST_FIXTURE",
    )

    # Parse using ResMed parser's internal method
    parser = ResmedEDFParser()
    unified_session = parser._parse_session_group(
        session_id=session_id, files=files, device_info=device_info, base_path=fixture_path
    )

    # Extract database path from SQLAlchemy session
    engine = db_session.get_bind()
    db_url = str(engine.url)

    # Extract path from sqlite:///path format
    if db_url.startswith("sqlite:///"):
        db_path = db_url[10:]
    else:
        raise ValueError(f"Unsupported database URL format: {db_url}")

    # Import using DatabaseManager
    with DatabaseManager(db_path=Path(db_path)) as db_manager:
        success = import_session(db_manager, unified_session, force=False)

        if not success:
            raise RuntimeError(f"Failed to import session for fixture {fixture_name}")

    # Query the imported session from SQLAlchemy to return it
    cpap_session = (
        db_session.query(CPAPSession)
        .filter(CPAPSession.device_session_id == unified_session.device_session_id)
        .first()
    )

    if cpap_session is None:
        raise RuntimeError(
            f"Session was imported but not found in database: {unified_session.device_session_id}"
        )

    return cpap_session


def get_fixture_metadata(fixture_name: str) -> dict:
    """
    Get metadata about a fixture.

    Args:
        fixture_name: Name of the fixture

    Returns:
        Dict with metadata (date, file count, file types, etc.)
    """
    fixture_path, files = load_real_session(fixture_name)

    # Extract date from BRP filename if available
    session_date = None
    if "BRP" in files:
        # Filename format: YYYYMMDD_HHMMSS_BRP.edf
        filename = files["BRP"].stem
        parts = filename.split("_")
        if len(parts) >= 2:
            date_str = parts[0]
            time_str = parts[1]
            session_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"

    # Calculate total size
    total_size = sum(f.stat().st_size for f in files.values())

    return {
        "name": fixture_name,
        "path": str(fixture_path),
        "session_date": session_date,
        "file_count": len(files),
        "file_types": list(files.keys()),
        "total_size_bytes": total_size,
        "total_size_kb": total_size / 1024,
    }


def list_fixtures_with_metadata() -> List[dict]:
    """
    List all available fixtures with their metadata.

    Returns:
        List of metadata dicts for each fixture
    """
    fixtures = get_available_fixtures()
    return [get_fixture_metadata(name) for name in fixtures]
