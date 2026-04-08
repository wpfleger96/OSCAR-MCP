"""Generic backup orchestrator for raw CPAP data files.

Delegates all file-layout knowledge to the DeviceParser. This service
handles path construction, serial validation, import-from-backup detection,
and error reporting — it never knows about manufacturer-specific files.
"""

from __future__ import annotations

import logging
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from snore.constants import DEFAULT_RAW_BACKUP_DIR
from snore.parsers.base import RawFileManifest

if TYPE_CHECKING:
    from collections.abc import Callable

    from snore.parsers.base import DeviceParser

logger = logging.getLogger(__name__)

_SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class BackupResult:
    """Result of a backup operation."""

    backup_root: Path
    """Device backup root path (e.g., ~/.snore/raw/<serial>/)."""

    manifest: RawFileManifest | None = None
    """Manifest of files that were backed up. None if backup was skipped."""

    files_copied: int = 0
    files_skipped: int = 0
    skipped_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def was_skipped(self) -> bool:
        return self.manifest is None


class BackupService:
    """Generic backup orchestrator. Delegates file operations to parsers."""

    def __init__(self, backup_root: Path | None = None) -> None:
        self.backup_root = backup_root or DEFAULT_RAW_BACKUP_DIR

    def backup_via_parser(
        self,
        parser: DeviceParser,
        source_root: Path,
        device_serial: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> BackupResult:
        """Orchestrate backup for any parser that supports it.

        Handles serial validation, path construction, import-from-backup
        detection, and error reporting. Delegates actual file copying to
        parser.backup_raw_data().

        Args:
            parser: The device parser for this data source.
            source_root: Path to the raw data (e.g., SD card root).
            device_serial: Device serial number.
            progress_callback: Optional callback for progress messages.

        Returns:
            BackupResult with the backup root path and manifest.

        Raises:
            ValueError: If the device serial is invalid.
            RuntimeError: If backup fails.
        """
        self._validate_serial(device_serial)
        dest_root = self.get_device_backup_root(device_serial)

        # Detect import-from-backup: source is already inside the backup dir
        try:
            if source_root.resolve().is_relative_to(self.backup_root.resolve()):
                reason = "Source is already within the backup directory"
                logger.debug(f"Skipping backup: {reason}")
                return BackupResult(
                    backup_root=dest_root,
                    skipped_reason=reason,
                )
        except ValueError:
            pass  # is_relative_to raises ValueError on Windows for different drives

        if progress_callback:
            progress_callback(f"Backing up raw files to {dest_root}")

        manifest = parser.backup_raw_data(
            source_root, dest_root, progress_callback=progress_callback
        )

        files_copied = manifest.total_files
        logger.debug(
            f"Backup complete: {files_copied} files, "
            f"{len(manifest.nights)} nights to {dest_root}"
        )
        if progress_callback:
            progress_callback(
                f"Backed up {files_copied} files ({len(manifest.nights)} nights)"
            )

        return BackupResult(
            backup_root=dest_root,
            manifest=manifest,
            files_copied=files_copied,
        )

    def get_device_backup_root(self, device_serial: str) -> Path:
        """Return the backup root path for a device serial."""
        self._validate_serial(device_serial)
        return self.backup_root / device_serial

    def has_backup(self, device_serial: str) -> bool:
        """Check if a backup exists for a device serial."""
        root = self.get_device_backup_root(device_serial)
        return root.is_dir()

    def list_backed_up_devices(self) -> list[str]:
        """Return list of device serial numbers that have backups."""
        if not self.backup_root.is_dir():
            return []
        return sorted(
            d.name
            for d in self.backup_root.iterdir()
            if d.is_dir() and _SERIAL_PATTERN.match(d.name)
        )

    def _validate_serial(self, device_serial: str) -> None:
        """Validate device serial for safe path construction."""
        if not device_serial:
            raise ValueError("Device serial number is empty")
        if not _SERIAL_PATTERN.match(device_serial):
            raise ValueError(
                f"Device serial '{device_serial}' contains invalid characters. "
                f"Expected alphanumeric, underscore, or hyphen."
            )
        # Bounds check: resolved path must be under backup_root
        resolved = (self.backup_root / device_serial).resolve()
        if not resolved.is_relative_to(self.backup_root.resolve()):
            raise ValueError(
                f"Device serial '{device_serial}' resolves to a path "
                f"outside the backup directory"
            )
