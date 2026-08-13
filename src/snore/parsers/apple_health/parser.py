"""Minimal detection class for Apple Health export paths.

Deliberately NOT registered in the parser registry — this parser is standalone
and consumed directly by the Apple Health importer layer.
"""

from __future__ import annotations

import zipfile

from pathlib import Path


class AppleHealthParser:
    """Detects Apple Health ``export.zip`` or ``export`` directory inputs."""

    def detect(self, path: Path) -> bool:
        """Return ``True`` if *path* is a supported Apple Health export format.

        Recognises:
        - A zip file containing an ``export.xml`` member.
        - A directory containing ``export.xml`` directly or under
          ``apple_health_export/``.
        """
        if path.is_dir():
            return (path / "export.xml").exists() or (
                path / "apple_health_export" / "export.xml"
            ).exists()

        try:
            with zipfile.ZipFile(path) as zf:
                return any(m.endswith("export.xml") for m in zf.namelist())
        except (zipfile.BadZipFile, OSError):
            return False
