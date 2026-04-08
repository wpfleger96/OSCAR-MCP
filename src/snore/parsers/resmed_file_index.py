"""ResMed file indexing utilities.

Stable public functions for scanning and grouping ResMed EDF files.
Used by ResmedEDFParser for parsing and by backup/export methods
for file discovery. These are extracted from ResmedEDFParser to
provide a clean API boundary — services never call parser internals.
"""

from __future__ import annotations

import logging
import os
import re

from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches ResMed DATALOG EDF filenames: YYYYMMDD_HHMMSS_TYPE.edf
_EDF_FILENAME_PATTERN = re.compile(r"\d{8}_\d{6}_[A-Z0-9]+\.edf")
_EDF_SESSION_PATTERN = re.compile(r"(\d{8}_\d{6})_([A-Z0-9]+)\.edf")


def is_resmed_root(path: Path) -> bool:
    """Check if path contains ResMed data signature (STR.edf + DATALOG)."""
    if not path.is_dir():
        return False
    return (path / "STR.edf").exists() and (path / "DATALOG").is_dir()


def get_night_date(timestamp: datetime) -> str:
    """Get the night date for a session using OSCAR's noon cutoff rule.

    Sessions starting before noon belong to the previous day's night.
    This matches ResMed's commercial software and OSCAR's behavior.

    Args:
        timestamp: Session start time.

    Returns:
        Night date as YYYYMMDD string.
    """
    if timestamp.hour < 12:
        night_date = (timestamp - timedelta(days=1)).date()
    else:
        night_date = timestamp.date()
    return night_date.strftime("%Y%m%d")


def scan_edf_files(datalog_dir: Path) -> list[Path]:
    """Scan for ResMed EDF files using os.scandir (faster than rglob).

    Recursively scans the DATALOG directory and pre-filters by the
    ResMed filename pattern (YYYYMMDD_HHMMSS_TYPE.edf).

    Args:
        datalog_dir: Directory to scan (typically source_root / "DATALOG").

    Returns:
        List of EDF file paths found.
    """
    edf_files: list[Path] = []

    def _scan_dir(path: Path) -> None:
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_file() and entry.name.endswith(".edf"):
                        if _EDF_FILENAME_PATTERN.match(entry.name):
                            edf_files.append(Path(entry.path))
                    elif entry.is_dir():
                        _scan_dir(Path(entry.path))
        except PermissionError:
            logger.warning(f"Permission denied accessing directory: {path}")

    _scan_dir(datalog_dir)
    return edf_files


def group_session_files(
    datalog_dir: Path,
) -> dict[str, dict[str, dict[str, Path]]]:
    """Group EDF files by night date (noon-to-noon periods).

    Multiple sessions within the same night (mask removals/bathroom breaks)
    are grouped together to match OSCAR's behavior.

    Args:
        datalog_dir: Directory containing DATALOG files.

    Returns:
        Dict mapping night_date -> session_id -> file_type -> Path.
        Example::

            {
                "20240621": {
                    "20240621_013454": {
                        "BRP": Path("...BRP.edf"),
                        "PLD": Path("...PLD.edf"),
                    },
                    "20240621_053022": {
                        "BRP": Path("...BRP.edf"),
                    },
                },
            }
    """
    groups: dict[str, dict[str, dict[str, Path]]] = {}

    for edf_file in scan_edf_files(datalog_dir):
        match = _EDF_SESSION_PATTERN.match(edf_file.name)
        if not match:
            continue

        session_id = match.group(1)
        file_type = match.group(2)

        timestamp = datetime.strptime(session_id, "%Y%m%d_%H%M%S")
        night = get_night_date(timestamp)

        if night not in groups:
            groups[night] = {}
        if session_id not in groups[night]:
            groups[night][session_id] = {}

        groups[night][session_id][file_type] = edf_file

    return groups


def flatten_night_files(
    grouped: dict[str, dict[str, dict[str, Path]]],
) -> dict[str, list[Path]]:
    """Flatten grouped session files to night_date -> flat list of paths.

    Useful for backup/export where we don't need the session_id/file_type
    structure, just all files for a given night.
    """
    result: dict[str, list[Path]] = {}
    for night_date, sessions in grouped.items():
        files: list[Path] = []
        for file_map in sessions.values():
            files.extend(file_map.values())
        result[night_date] = files
    return result
