"""Unit tests for ExportService (raw, CSV, JSON exports)."""

from __future__ import annotations

import csv
import json
import zipfile

from collections.abc import Generator
from datetime import date, datetime
from pathlib import Path

import pytest

from sqlalchemy.orm import Session as DBSession

from snore.services.export_service import ExportService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backup_root(tmp_path: Path) -> Path:
    return tmp_path / "raw"


@pytest.fixture()
def export_service(backup_root: Path) -> ExportService:
    return ExportService(backup_root=backup_root)


@pytest.fixture()
def fake_backup(backup_root: Path) -> Path:
    """Create a fake ResMed backup directory that looks like an SD card root."""
    device = backup_root / "SN12345"
    device.mkdir(parents=True)

    # STR.edf — minimal valid EDF header (256 bytes)
    header = bytearray(256)
    # version (bytes 0-7)
    header[0:8] = b"0       "
    # start date (bytes 168-175): 01.08.25
    header[168:176] = b"01.08.25"
    # start time (bytes 176-183): 00.00.00
    header[176:184] = b"00.00.00"
    # num records (bytes 236-243)
    header[236:244] = b"30      "
    # record duration (bytes 244-251)
    header[244:252] = b"86400   "
    # num signals (bytes 252-255)
    header[252:256] = b"1   "
    (device / "STR.edf").write_bytes(bytes(header))
    (device / "Identification.json").write_text('{"serial": "SN12345"}')

    datalog = device / "DATALOG"
    # Night 1: 20250806 (session at 01:34 AM → night of 20250806)
    d1 = datalog / "20250807"
    d1.mkdir(parents=True)
    (d1 / "20250807_013454_BRP.edf").write_bytes(b"brp1")
    (d1 / "20250807_013454_PLD.edf").write_bytes(b"pld1")

    # Night 2: 20250807 (session at 22:00 → night of 20250807)
    d2 = datalog / "20250807"  # same folder, different session time
    (d2 / "20250807_220000_BRP.edf").write_bytes(b"brp2")
    (d2 / "20250807_220000_PLD.edf").write_bytes(b"pld2")

    # Night 3: 20250809 (session at 23:00 → night of 20250809)
    d3 = datalog / "20250809"
    d3.mkdir(parents=True)
    (d3 / "20250809_230000_BRP.edf").write_bytes(b"brp3")

    return device


# ---------------------------------------------------------------------------
# export_raw
# ---------------------------------------------------------------------------


class TestExportRaw:
    def test_export_to_directory(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export"
        result = export_service.export_raw(out, device_serial="SN12345")

        assert result.format == "raw"
        assert result.nights_exported > 0
        assert result.files_written > 0
        assert (out / "STR.edf").exists()
        assert (out / "Identification.json").exists()

    def test_export_to_zip(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export.zip"
        result = export_service.export_raw(out, device_serial="SN12345", as_zip=True)

        assert result.format == "raw"
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert "STR.edf" in names
            assert "Identification.json" in names

    def test_export_zip_by_extension(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export.zip"
        export_service.export_raw(out, device_serial="SN12345")
        assert out.exists()
        assert zipfile.is_zipfile(out)

    def test_dry_run(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export"
        result = export_service.export_raw(out, device_serial="SN12345", dry_run=True)

        assert result.nights_exported > 0
        assert result.files_written > 0
        assert not out.exists()

    def test_date_filter(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export"
        result = export_service.export_raw(
            out,
            device_serial="SN12345",
            date_from=date(2025, 8, 9),
            date_to=date(2025, 8, 9),
        )

        # Only night 20250809 should be included
        assert result.nights_exported == 1
        # Device files always included
        assert (out / "STR.edf").exists()

    def test_date_filter_warns(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export"
        result = export_service.export_raw(
            out,
            device_serial="SN12345",
            date_from=date(2025, 8, 9),
            date_to=date(2025, 8, 9),
        )
        assert any("outside" in w.lower() for w in result.warnings)

    def test_auto_resolves_single_device(
        self, export_service: ExportService, fake_backup: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "export"
        # Don't specify device — should auto-detect the single one
        result = export_service.export_raw(out)
        assert result.nights_exported > 0

    def test_multiple_devices_no_serial_raises(
        self, export_service: ExportService, backup_root: Path, fake_backup: Path
    ) -> None:
        (backup_root / "SN99999").mkdir()
        with pytest.raises(ValueError, match="Multiple devices"):
            export_service.export_raw(Path("/tmp/out"))

    def test_no_backup_raises(self, tmp_path: Path) -> None:
        svc = ExportService(backup_root=tmp_path / "nonexistent")
        with pytest.raises(FileNotFoundError):
            svc.export_raw(Path("/tmp/out"), device_serial="NOPE")


# ---------------------------------------------------------------------------
# export_csv and export_json (with in-memory SQLite)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session(tmp_path: Path) -> Generator[DBSession]:
    """Create a fresh SQLite DB with test data per test."""
    import snore.database.session as db_mod

    from snore.database import models

    db_path = str(tmp_path / "test.db")

    # Reset the singleton so each test gets a fresh DB
    db_mod._engine = None
    db_mod._session_factory = None
    db_mod.init_database(db_path)

    with db_mod.session_scope() as session:
        device = models.Device(
            manufacturer="ResMed",
            model="AirSense 11",
            serial_number="SN12345",
        )
        session.add(device)
        session.flush()

        s = models.Session(
            device_id=device.id,
            device_session_id="20250807_220000",
            start_time=datetime(2025, 8, 7, 22, 0, 0),
            end_time=datetime(2025, 8, 8, 6, 0, 0),
            duration_seconds=28800.0,
            therapy_mode="APAP",
            import_source="resmed_edf",
            has_waveform_data=False,
            has_event_data=True,
            has_statistics=True,
            enabled=True,
        )
        session.add(s)
        session.flush()

        session.add(
            models.Statistics(
                session_id=s.id,
                ahi=3.5,
                oai=1.0,
                cai=0.5,
                hi=2.0,
                obstructive_apneas=8,
                central_apneas=4,
                hypopneas=16,
                pressure_mean=10.5,
                pressure_95th=12.0,
                leak_mean=5.0,
                leak_95th=15.0,
                usage_hours=8.0,
            )
        )

        session.add(
            models.Event(
                session_id=s.id,
                event_type="OA",
                start_time=datetime(2025, 8, 7, 23, 15, 0),
                duration_seconds=12.5,
            )
        )

        session.add(models.Setting(session_id=s.id, key="mode", value="APAP"))
        session.add(models.Setting(session_id=s.id, key="pressure_min", value="6.0"))

        session.commit()
        yield session

    # Reset for next test
    db_mod._engine = None
    db_mod._session_factory = None


class TestExportCsv:
    def test_creates_csv_files(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "csv_export"
        result = svc.export_csv(db_session, out)

        assert result.format == "csv"
        assert (out / "sessions.csv").exists()
        assert (out / "events.csv").exists()
        assert (out / "settings.csv").exists()
        assert result.files_written == 3

    def test_sessions_csv_content(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "csv_export"
        svc.export_csv(db_session, out)

        with open(out / "sessions.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        row = rows[0]
        assert row["device_session_id"] == "20250807_220000"
        assert row["device_serial"] == "SN12345"
        assert row["ahi"] == "3.5"
        assert row["timezone"] == "local"

    def test_events_csv_content(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "csv_export"
        svc.export_csv(db_session, out)

        with open(out / "events.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["event_type"] == "OA"
        assert rows[0]["timezone"] == "local"

    def test_settings_csv_content(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "csv_export"
        svc.export_csv(db_session, out)

        with open(out / "settings.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        keys = {r["key"] for r in rows}
        assert "mode" in keys
        assert "pressure_min" in keys

    def test_date_filter_excludes(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "csv_export"
        result = svc.export_csv(db_session, out, date_from=date(2026, 1, 1))
        assert result.nights_exported == 0

    def test_no_sessions_warns(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "csv_export"
        result = svc.export_csv(db_session, out, date_from=date(2099, 1, 1))
        assert any("No sessions" in w for w in result.warnings)


class TestExportJson:
    def test_creates_json_file(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "export.json"
        result = svc.export_json(db_session, out)

        assert result.format == "json"
        assert out.exists()
        assert result.files_written == 1

    def test_json_structure(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "export.json"
        svc.export_json(db_session, out)

        with open(out) as f:
            doc = json.load(f)

        assert "exported_at" in doc
        assert "sessions" in doc
        assert doc["session_count"] == 1

        s = doc["sessions"][0]
        assert s["device_session_id"] == "20250807_220000"
        assert s["timezone"] == "local"
        assert s["device"]["serial"] == "SN12345"
        assert s["statistics"]["ahi"] == 3.5
        assert len(s["events"]) == 1
        assert s["events"][0]["event_type"] == "OA"
        assert s["settings"]["mode"] == "APAP"

    def test_date_filter(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "export.json"
        svc.export_json(db_session, out, date_from=date(2099, 1, 1))

        with open(out) as f:
            doc = json.load(f)
        assert doc["session_count"] == 0

    def test_device_filter(self, db_session: DBSession, tmp_path: Path) -> None:
        svc = ExportService()
        out = tmp_path / "export.json"
        svc.export_json(db_session, out, device_serial="NONEXISTENT")

        with open(out) as f:
            doc = json.load(f)
        assert doc["session_count"] == 0
