"""
Abstract Parser Interface

This module defines the base class that ALL device parsers must implement.
This ensures a consistent interface across all manufacturers and file formats.

Key Principle: Any new device parser just needs to inherit from DeviceParser
and implement these methods - the rest of the system automatically works.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from snore.parsers.types import ParserMetadata
from snore.parsers.unified import DeviceInfo, UnifiedSession

__all__ = [
    "DeviceParser",
    "ParserDetectionResult",
    "ParserMetadata",
    "RawFileManifest",
]


class ParserDetectionResult:
    """Result of parser detection scan."""

    def __init__(
        self,
        detected: bool,
        confidence: float = 1.0,
        device_info: DeviceInfo | None = None,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        self.detected = detected
        self.confidence = confidence  # 0.0 to 1.0
        self.device_info = device_info
        self.message = message
        self.metadata = metadata or {}


@dataclass
class RawFileManifest:
    """Generic manifest of raw files grouped by night date.

    Used by BackupService and ExportService to copy files without
    knowing manufacturer-specific file layouts.
    """

    device_files: list[Path] = field(default_factory=list)
    """Device-level files (e.g., settings summary, identification). Copied once."""

    nights: dict[date, list[Path]] = field(default_factory=dict)
    """Per-night session files keyed by night date."""

    source_root: Path = field(default_factory=lambda: Path("."))
    """Root path that all file paths are relative children of.
    Export uses file.relative_to(source_root) for destination path construction."""

    files_copied: int = 0
    """Number of files that were newly copied (not skipped)."""

    files_skipped: int = 0
    """Number of files that were skipped (already existed with matching size/mtime)."""

    @property
    def total_files(self) -> int:
        """Total number of files across device files and all nights."""
        return len(self.device_files) + sum(
            len(files) for files in self.nights.values()
        )

    @property
    def total_bytes(self) -> int:
        """Total size of all files in bytes."""
        total = sum(f.stat().st_size for f in self.device_files if f.exists())
        for files in self.nights.values():
            total += sum(f.stat().st_size for f in files if f.exists())
        return total


class DeviceParser(ABC):
    """
    Abstract base class for all CPAP device parsers.

    Every parser (ResMed, Philips, OSCAR binary, etc.) must inherit from
    this class and implement all abstract methods. This ensures consistency
    and allows the system to work with any parser without modification.

    Usage Example:
        class ResmedEDFParser(DeviceParser):
            def detect(self, path):
                return (path / "STR.edf").exists()

            def parse_sessions(self, path):
                yield unified_session

    The rest of the system doesn't need to know anything about ResMed!
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        self._metadata = self.get_metadata()

    @abstractmethod
    def get_metadata(self) -> ParserMetadata:
        """
        Return metadata about this parser.

        Returns:
            ParserMetadata with parser information

        Example:
            return ParserMetadata(
                parser_id="resmed_edf",
                parser_version="1.0.0",
                manufacturer="ResMed",
                supported_formats=["EDF+", "EDF"],
                supported_models=["AirSense 10", "AirSense 11"],
                description="Parser for ResMed EDF+ files"
            )
        """

    @abstractmethod
    def detect(self, path: Path) -> ParserDetectionResult:
        """
        Detect if this parser can handle the data at the given path.

        This method should quickly check if the directory/file structure
        matches what this parser expects. It should NOT do full parsing.

        Args:
            path: Path to directory or file to check

        Returns:
            ParserDetectionResult indicating if this parser can handle the data

        Example:
            str_file = path / "STR.edf"
            datalog = path / "DATALOG"
            if str_file.exists() and datalog.is_dir():
                return ParserDetectionResult(
                    detected=True,
                    confidence=1.0,
                    message="Found ResMed EDF+ structure"
                )
            return ParserDetectionResult(detected=False)
        """

    @abstractmethod
    def get_device_info(self, path: Path) -> DeviceInfo:
        """
        Extract device information from the data files.

        This is called after detection succeeds to get basic device info
        before doing full parsing. Should be fast.

        Args:
            path: Path to data directory/file

        Returns:
            DeviceInfo with manufacturer, model, serial number, etc.

        Raises:
            ParserError: If device info cannot be extracted
        """
        pass

    @abstractmethod
    def parse_sessions(
        self,
        path: Path,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int | None = None,
        sort_by: str | None = None,
        parallel: bool = True,
        progress_callback: Callable[[str], None] | None = None,
    ) -> Iterator[UnifiedSession]:
        """
        Parse all sessions from the given path and yield unified sessions.

        This is the core parsing method. It should:
        1. Locate all session data files
        2. Parse each session's native format
        3. Convert to UnifiedSession format
        4. Yield one UnifiedSession at a time (memory efficient)

        Args:
            path: Path to data directory/file
            date_from: Optional start date filter (ISO format: YYYY-MM-DD)
            date_to: Optional end date filter (ISO format: YYYY-MM-DD)
            limit: Optional maximum number of sessions to yield
            sort_by: Optional sort order - "date-asc", "date-desc", or None for filesystem order
            progress_callback: Optional callback invoked with a progress message string as each session is parsed

        Yields:
            UnifiedSession objects

        Raises:
            ParserError: If parsing fails

        Example:
            for session_file in self._find_session_files(path):
                native_data = self._parse_native_format(session_file)

                unified = self._to_unified_session(native_data)

                yield unified
        """

    def parse_single_session(
        self, path: Path, session_id: str
    ) -> UnifiedSession | None:
        """
        Parse a single specific session by ID.

        Default implementation iterates through all sessions. Subclasses
        can override for more efficient direct lookup.

        Args:
            path: Path to data directory
            session_id: Device-specific session identifier

        Returns:
            UnifiedSession or None if not found
        """
        for session in self.parse_sessions(path):
            if session.device_session_id == session_id:
                return session
        return None

    # ------------------------------------------------------------------
    # Raw file backup / export (optional — override in subclasses)
    # ------------------------------------------------------------------

    @property
    def supports_raw_backup(self) -> bool:
        """Whether this parser supports raw file backup and export.

        Parsers that read from already-backed-up data (e.g., OSCAR binary
        cache) should leave this as False.
        """
        return False

    def backup_raw_data(
        self,
        source_root: Path,
        dest_root: Path,
        progress_callback: Callable[[str], None] | None = None,
    ) -> RawFileManifest:
        """Copy raw data files from source to dest, preserving layout.

        Skips files that already exist at dest with matching size and mtime.
        Returns a RawFileManifest describing what was backed up.

        The parser knows its own file structure and versioning rules (e.g.,
        STR.edf historical snapshots for ResMed). The service layer calls
        this method generically without knowing file layout details.

        MUST be stateless — use only the passed path arguments, never
        instance state from detect() (which may be stale or from a
        different path).

        Args:
            source_root: Path to raw data (e.g., SD card root).
            dest_root: Backup destination (e.g., ~/.snore/raw/<serial>/).
            progress_callback: Optional callback for progress messages.

        Raises:
            NotImplementedError: If this parser does not support raw backup.
        """
        raise NotImplementedError(
            f"Parser '{self.parser_id}' does not support raw file backup"
        )

    def get_raw_file_manifest(
        self,
        root: Path,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> RawFileManifest:
        """Return manifest of raw files at root, optionally filtered by date.

        Used by ExportService to enumerate files for export without knowing
        the manufacturer-specific file layout.

        MUST be stateless — same requirements as backup_raw_data().

        Raises:
            NotImplementedError: If this parser does not support raw file manifest.
        """
        raise NotImplementedError(
            f"Parser '{self.parser_id}' does not support raw file manifest"
        )

    def trim_device_summary(
        self,
        output_root: Path,
        date_from: date,
        date_to: date,
    ) -> None:
        """Trim device-level summary files to the given date range.

        Called after files are copied to the export output directory.
        Parsers that have a trimmable summary file (e.g., ResMed's STR.edf)
        override this to rewrite the file in-place.

        Default: no-op.
        """
        return  # noqa: B027

    @property
    def metadata(self) -> ParserMetadata:
        """Get parser metadata."""
        return self._metadata

    @property
    def parser_id(self) -> str:
        """Get unique parser identifier."""
        return self._metadata.parser_id

    @property
    def manufacturer(self) -> str:
        """Get manufacturer name this parser handles."""
        return self._metadata.manufacturer

    @property
    def supported_formats(self) -> list[str]:
        """Get list of file formats this parser supports."""
        return self._metadata.supported_formats

    def __str__(self) -> str:
        """String representation of parser."""
        return f"{self.parser_id} (v{self._metadata.parser_version}): {self._metadata.description}"

    def __repr__(self) -> str:
        """Developer representation of parser."""
        return f"<{self.__class__.__name__} id={self.parser_id} manufacturer={self.manufacturer}>"


class ParserError(Exception):
    """Base exception for parser errors."""

    def __init__(self, message: str, parser: DeviceParser | None = None):
        super().__init__(message)
        self.parser = parser
