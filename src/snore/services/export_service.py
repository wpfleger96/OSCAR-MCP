"""Unified export service for CPAP data in multiple formats.

Supports:
- raw: OSCAR-compatible SD card directory reconstruction from backups
- csv: Parsed data as CSV files (sessions, events, settings, optional waveforms)
- json: Parsed data as a single JSON document
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import zipfile

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from snore.constants import DEFAULT_RAW_BACKUP_DIR
from snore.parsers.base import RawFileManifest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    """Result of an export operation."""

    format: str
    output_path: Path
    nights_exported: int = 0
    files_written: int = 0
    total_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


class ExportService:
    """Unified export service for CPAP data."""

    def __init__(self, backup_root: Path | None = None) -> None:
        self.backup_root = backup_root or DEFAULT_RAW_BACKUP_DIR

    # ------------------------------------------------------------------
    # Raw export (filesystem only, no DB)
    # ------------------------------------------------------------------

    def export_raw(
        self,
        output: Path,
        date_from: date | None = None,
        date_to: date | None = None,
        device_serial: str | None = None,
        as_zip: bool = False,
        dry_run: bool = False,
        trim_str: bool = False,
    ) -> ExportResult:
        """Export raw backup files as an OSCAR-compatible directory.

        Works entirely from the filesystem — no database needed.
        """
        from snore.parsers.register_all import register_all_parsers
        from snore.parsers.registry import parser_registry

        register_all_parsers()

        serial = self._resolve_device_serial(device_serial)
        device_root = self.backup_root / serial

        if not device_root.is_dir():
            raise FileNotFoundError(
                f"No backup found for device '{serial}' at {device_root}.\n"
                "Run 'snore import /path/to/sd' to create a backup first."
            )

        parser = parser_registry.detect_parser(device_root)
        if parser is None:
            raise RuntimeError(f"No parser recognized the backup at {device_root}")

        if not parser.supports_raw_backup:
            raise RuntimeError(
                f"Parser '{parser.parser_id}' does not support raw export"
            )

        manifest = parser.get_raw_file_manifest(device_root, date_from, date_to)

        if dry_run:
            return self._dry_run_raw(manifest, output, date_from, date_to)

        warnings: list[str] = []
        if (date_from or date_to) and manifest.device_files and not trim_str:
            warnings.append(
                "Device-level files may contain data outside the exported date range. "
                "Use --trim-str to trim STR.edf to the date range."
            )

        if as_zip or str(output).endswith(".zip"):
            result = self._export_raw_zip(manifest, output, warnings)
        else:
            result = self._export_raw_dir(manifest, output, warnings)

        if (
            trim_str
            and date_from
            and date_to
            and not (as_zip or str(output).endswith(".zip"))
        ):
            parser.trim_device_summary(output, date_from, date_to)

        return result

    def _dry_run_raw(
        self,
        manifest: RawFileManifest,
        output: Path,
        date_from: date | None,
        date_to: date | None,
    ) -> ExportResult:
        """Return what would be exported without copying."""
        return ExportResult(
            format="raw",
            output_path=output,
            nights_exported=len(manifest.nights),
            files_written=manifest.total_files,
            total_bytes=manifest.total_bytes,
        )

    def _export_raw_dir(
        self,
        manifest: RawFileManifest,
        output: Path,
        warnings: list[str],
    ) -> ExportResult:
        """Copy raw files to a directory."""
        output.mkdir(parents=True, exist_ok=True)
        files_written = 0

        for src in manifest.device_files:
            dest = output / src.relative_to(manifest.source_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            files_written += 1

        for _night_date, files in sorted(manifest.nights.items()):
            for src in files:
                dest = output / src.relative_to(manifest.source_root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                files_written += 1

        return ExportResult(
            format="raw",
            output_path=output,
            nights_exported=len(manifest.nights),
            files_written=files_written,
            warnings=warnings,
        )

    def _export_raw_zip(
        self,
        manifest: RawFileManifest,
        output: Path,
        warnings: list[str],
    ) -> ExportResult:
        """Write raw files to a zip archive."""
        output.parent.mkdir(parents=True, exist_ok=True)
        files_written = 0

        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for src in manifest.device_files:
                arcname = str(src.relative_to(manifest.source_root))
                zf.write(src, arcname)
                files_written += 1

            for _night_date, files in sorted(manifest.nights.items()):
                for src in files:
                    arcname = str(src.relative_to(manifest.source_root))
                    zf.write(src, arcname)
                    files_written += 1

        return ExportResult(
            format="raw",
            output_path=output,
            nights_exported=len(manifest.nights),
            files_written=files_written,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # CSV export (from database)
    # ------------------------------------------------------------------

    def export_csv(
        self,
        db_session: DBSession,
        output: Path,
        date_from: date | None = None,
        date_to: date | None = None,
        device_serial: str | None = None,
        include_waveforms: bool = False,
    ) -> ExportResult:
        """Export parsed data as CSV files."""
        from snore.database import models

        output.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        files_written = 0
        nights: set[date] = set()

        sessions = self._query_sessions(db_session, date_from, date_to, device_serial)

        if not sessions:
            warnings.append("No sessions found for the specified filters.")
            return ExportResult(format="csv", output_path=output, warnings=warnings)

        # sessions.csv
        sessions_path = output / "sessions.csv"
        with open(sessions_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "device_session_id",
                    "date",
                    "start_time",
                    "end_time",
                    "duration_hours",
                    "device_serial",
                    "device_model",
                    "therapy_mode",
                    "timezone",
                    "ahi",
                    "oai",
                    "cai",
                    "hi",
                    "obstructive_apneas",
                    "central_apneas",
                    "hypopneas",
                    "reras",
                    "pressure_mean",
                    "pressure_95th",
                    "epap_mean",
                    "leak_mean",
                    "leak_95th",
                    "spo2_mean",
                    "usage_hours",
                ]
            )
            for s in sessions:
                night = (
                    s["start_time"].date()
                    if s["start_time"].hour >= 12
                    else (s["start_time"] - timedelta(days=1)).date()
                )
                nights.add(night)
                stats = s.get("statistics") or {}
                writer.writerow(
                    [
                        s["device_session_id"],
                        s["start_time"].strftime("%Y-%m-%d"),
                        s["start_time"].isoformat(),
                        s["end_time"].isoformat(),
                        round(s["duration_seconds"] / 3600, 2)
                        if s["duration_seconds"]
                        else "",
                        s["serial_number"],
                        s["model"],
                        s["therapy_mode"],
                        "local",
                        stats.get("ahi", ""),
                        stats.get("oai", ""),
                        stats.get("cai", ""),
                        stats.get("hi", ""),
                        stats.get("obstructive_apneas", ""),
                        stats.get("central_apneas", ""),
                        stats.get("hypopneas", ""),
                        stats.get("reras", ""),
                        stats.get("pressure_mean", ""),
                        stats.get("pressure_95th", ""),
                        stats.get("epap_mean", ""),
                        stats.get("leak_mean", ""),
                        stats.get("leak_95th", ""),
                        stats.get("spo2_mean", ""),
                        stats.get("usage_hours", ""),
                    ]
                )
        files_written += 1

        # events.csv
        events_path = output / "events.csv"
        with open(events_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "device_session_id",
                    "session_date",
                    "event_type",
                    "start_time",
                    "duration_seconds",
                    "timezone",
                ]
            )
            for s in sessions:
                events = (
                    db_session.query(models.Event)
                    .filter_by(session_id=s["id"])
                    .order_by(models.Event.start_time)
                    .all()
                )
                for e in events:
                    writer.writerow(
                        [
                            s["device_session_id"],
                            s["start_time"].strftime("%Y-%m-%d"),
                            e.event_type,
                            e.start_time.isoformat() if e.start_time else "",
                            e.duration_seconds or "",
                            "local",
                        ]
                    )
        files_written += 1

        # settings.csv
        settings_path = output / "settings.csv"
        with open(settings_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "device_session_id",
                    "session_date",
                    "key",
                    "value",
                ]
            )
            for s in sessions:
                settings = (
                    db_session.query(models.Setting)
                    .filter_by(session_id=s["id"])
                    .order_by(models.Setting.key)
                    .all()
                )
                for st in settings:
                    writer.writerow(
                        [
                            s["device_session_id"],
                            s["start_time"].strftime("%Y-%m-%d"),
                            st.key,
                            st.value,
                        ]
                    )
        files_written += 1

        # waveforms (optional)
        if include_waveforms:
            waveforms_dir = output / "waveforms"
            waveforms_dir.mkdir(exist_ok=True)
            warnings.append(
                "Waveform export enabled. Files may be very large "
                "(~720K rows per 8-hour session at 25Hz)."
            )
            for s in sessions:
                waveforms = (
                    db_session.query(models.Waveform)
                    .filter_by(session_id=s["id"])
                    .all()
                )
                for w in waveforms:
                    if not w.data_blob:
                        continue
                    fname = (
                        f"{s['serial_number']}_{s['start_time']:%Y%m%d}_"
                        f"{s['start_time']:%H%M%S}_{w.waveform_type}.csv"
                    )
                    wpath = waveforms_dir / fname
                    data = np.frombuffer(w.data_blob, dtype=np.float32)
                    if len(data) % 2 != 0:
                        continue
                    data = data.reshape(-1, 2)
                    with open(wpath, "w", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(["offset_seconds", "value"])
                        for row in data:
                            writer.writerow([f"{row[0]:.3f}", f"{row[1]:.3f}"])
                    files_written += 1

        return ExportResult(
            format="csv",
            output_path=output,
            nights_exported=len(nights),
            files_written=files_written,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # JSON export (from database)
    # ------------------------------------------------------------------

    def export_json(
        self,
        db_session: DBSession,
        output: Path,
        date_from: date | None = None,
        date_to: date | None = None,
        device_serial: str | None = None,
    ) -> ExportResult:
        """Export parsed data as a single JSON document."""
        from snore.database import models

        output.parent.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []

        sessions = self._query_sessions(db_session, date_from, date_to, device_serial)

        if not sessions:
            warnings.append("No sessions found for the specified filters.")

        session_list: list[dict[str, Any]] = []
        nights: set[date] = set()

        for s in sessions:
            night = (
                s["start_time"].date()
                if s["start_time"].hour >= 12
                else (s["start_time"] - timedelta(days=1)).date()
            )
            nights.add(night)

            events = (
                db_session.query(models.Event)
                .filter_by(session_id=s["id"])
                .order_by(models.Event.start_time)
                .all()
            )
            settings = (
                db_session.query(models.Setting)
                .filter_by(session_id=s["id"])
                .order_by(models.Setting.key)
                .all()
            )

            session_list.append(
                {
                    "device_session_id": s["device_session_id"],
                    "date": s["start_time"].strftime("%Y-%m-%d"),
                    "start_time": s["start_time"].isoformat(),
                    "end_time": s["end_time"].isoformat(),
                    "duration_hours": round(s["duration_seconds"] / 3600, 2)
                    if s["duration_seconds"]
                    else None,
                    "timezone": "local",
                    "device": {
                        "serial": s["serial_number"],
                        "model": s["model"],
                        "manufacturer": s["manufacturer"],
                    },
                    "statistics": s.get("statistics") or {},
                    "settings": {st.key: st.value for st in settings},
                    "events": [
                        {
                            "event_type": e.event_type,
                            "start_time": e.start_time.isoformat()
                            if e.start_time
                            else None,
                            "duration_seconds": e.duration_seconds,
                        }
                        for e in events
                    ],
                }
            )

        doc = {
            "exported_at": datetime.now().isoformat(),
            "snore_export_format": "1.0",
            "date_range": {
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None,
            },
            "session_count": len(session_list),
            "sessions": session_list,
        }

        with open(output, "w") as f:
            json.dump(doc, f, indent=2, default=str)

        return ExportResult(
            format="json",
            output_path=output,
            nights_exported=len(nights),
            files_written=1,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_device_serial(self, device_serial: str | None) -> str:
        """Resolve device serial, failing fast on ambiguity."""
        if device_serial:
            return device_serial

        if not self.backup_root.is_dir():
            raise FileNotFoundError(
                f"No backup directory found at {self.backup_root}.\n"
                "Run 'snore import /path/to/sd' to create backups first."
            )

        devices = [
            d.name
            for d in self.backup_root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        if len(devices) == 0:
            raise FileNotFoundError(f"No device backups found in {self.backup_root}.")
        if len(devices) == 1:
            return devices[0]

        device_list = "\n  ".join(devices)
        raise ValueError(
            f"Multiple devices found. Specify one with --device:\n  {device_list}"
        )

    def _query_sessions(
        self,
        db_session: DBSession,
        date_from: date | None,
        date_to: date | None,
        device_serial: str | None,
    ) -> list[dict[str, Any]]:
        """Query sessions with optional filters, returning dicts with joined data."""
        from sqlalchemy import text

        where_clauses = ["sessions.enabled = 1"]
        params: dict[str, Any] = {}

        if device_serial:
            where_clauses.append("devices.serial_number = :device")
            params["device"] = device_serial
        if date_from:
            where_clauses.append("sessions.start_time >= :from_date")
            params["from_date"] = datetime(
                date_from.year, date_from.month, date_from.day
            ).isoformat()
        if date_to:
            where_clauses.append("sessions.start_time <= :to_date")
            params["to_date"] = datetime(
                date_to.year, date_to.month, date_to.day, 23, 59, 59
            ).isoformat()

        where_sql = " AND ".join(where_clauses)

        query = text(f"""
            SELECT
                sessions.id,
                sessions.device_session_id,
                sessions.start_time,
                sessions.end_time,
                sessions.duration_seconds,
                sessions.therapy_mode,
                devices.serial_number,
                devices.model,
                devices.manufacturer,
                statistics.ahi, statistics.oai, statistics.cai, statistics.hi,
                statistics.obstructive_apneas, statistics.central_apneas,
                statistics.hypopneas, statistics.reras,
                statistics.pressure_mean, statistics.pressure_95th,
                statistics.epap_mean,
                statistics.leak_mean, statistics.leak_95th,
                statistics.spo2_mean, statistics.usage_hours
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            LEFT JOIN statistics ON sessions.id = statistics.session_id
            WHERE {where_sql}
            ORDER BY sessions.start_time ASC
        """)

        rows = db_session.execute(query, params).fetchall()
        results = []
        for row in rows:
            r = dict(row._mapping)
            for dt_field in ("start_time", "end_time"):
                if isinstance(r[dt_field], str):
                    r[dt_field] = datetime.fromisoformat(r[dt_field])
            stat_keys = [
                "ahi",
                "oai",
                "cai",
                "hi",
                "obstructive_apneas",
                "central_apneas",
                "hypopneas",
                "reras",
                "pressure_mean",
                "pressure_95th",
                "epap_mean",
                "leak_mean",
                "leak_95th",
                "spo2_mean",
                "usage_hours",
            ]
            r["statistics"] = {k: r.pop(k) for k in stat_keys if r.get(k) is not None}
            # Clean up None stat keys
            for k in stat_keys:
                r.pop(k, None)
            results.append(r)

        return results
