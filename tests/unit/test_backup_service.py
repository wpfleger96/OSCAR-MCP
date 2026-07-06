"""Unit tests for BackupService and related backup functionality."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from snore.parsers.base import DeviceParser, RawFileManifest
from snore.services.backup_service import BackupService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backup_root(tmp_path: Path) -> Path:
    return tmp_path / "raw"


@pytest.fixture()
def backup_service(backup_root: Path) -> BackupService:
    return BackupService(backup_root=backup_root)


@pytest.fixture()
def fake_sd(tmp_path: Path) -> Path:
    """Create a minimal fake ResMed SD card structure."""
    sd = tmp_path / "sd_card"
    sd.mkdir()
    (sd / "STR.edf").write_bytes(b"\x00" * 256)
    (sd / "Identification.json").write_text('{"serial": "ABC123"}')
    datalog = sd / "DATALOG" / "20250807"
    datalog.mkdir(parents=True)
    (datalog / "20250807_013454_BRP.edf").write_bytes(b"brp_data")
    (datalog / "20250807_013454_PLD.edf").write_bytes(b"pld_data")
    return sd


def _make_mock_parser(
    supports_backup: bool = True,
    backup_return: RawFileManifest | None = None,
) -> MagicMock:
    """Create a mock DeviceParser with backup support."""
    parser = MagicMock(spec=DeviceParser)
    parser.supports_raw_backup = supports_backup
    parser.parser_id = "test_parser"
    if backup_return:
        parser.backup_raw_data.return_value = backup_return
    else:
        parser.backup_raw_data.return_value = RawFileManifest(
            source_root=Path("/tmp/test"),
        )
    return parser


# ---------------------------------------------------------------------------
# BackupService._validate_serial
# ---------------------------------------------------------------------------


class TestSerialValidation:
    def test_valid_serial(self, backup_service: BackupService) -> None:
        backup_service._validate_serial("ABC12345")

    def test_valid_serial_with_underscore(self, backup_service: BackupService) -> None:
        backup_service._validate_serial("SN_12345-678")

    def test_empty_serial_rejected(self, backup_service: BackupService) -> None:
        with pytest.raises(ValueError, match="empty"):
            backup_service._validate_serial("")

    def test_path_traversal_rejected(self, backup_service: BackupService) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            backup_service._validate_serial("../../../etc")

    def test_slash_rejected(self, backup_service: BackupService) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            backup_service._validate_serial("foo/bar")

    def test_space_rejected(self, backup_service: BackupService) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            backup_service._validate_serial("foo bar")


# ---------------------------------------------------------------------------
# BackupService.get_device_backup_root
# ---------------------------------------------------------------------------


class TestDeviceBackupRoot:
    def test_path_construction(
        self, backup_service: BackupService, backup_root: Path
    ) -> None:
        result = backup_service.get_device_backup_root("SN12345")
        assert result == backup_root / "SN12345"

    def test_invalid_serial_raises(self, backup_service: BackupService) -> None:
        with pytest.raises(ValueError):
            backup_service.get_device_backup_root("../../bad")


# ---------------------------------------------------------------------------
# BackupService.has_backup / list_backed_up_devices
# ---------------------------------------------------------------------------


class TestBackupDiscovery:
    def test_has_backup_false_when_empty(self, backup_service: BackupService) -> None:
        assert not backup_service.has_backup("SN12345")

    def test_has_backup_true(
        self, backup_service: BackupService, backup_root: Path
    ) -> None:
        (backup_root / "SN12345").mkdir(parents=True)
        assert backup_service.has_backup("SN12345")

    def test_list_empty(self, backup_service: BackupService) -> None:
        assert backup_service.list_backed_up_devices() == []

    def test_list_devices(
        self, backup_service: BackupService, backup_root: Path
    ) -> None:
        (backup_root / "SN111").mkdir(parents=True)
        (backup_root / "SN222").mkdir(parents=True)
        assert backup_service.list_backed_up_devices() == ["SN111", "SN222"]

    def test_list_ignores_non_dirs(
        self, backup_service: BackupService, backup_root: Path
    ) -> None:
        backup_root.mkdir(parents=True)
        (backup_root / "not_a_dir.txt").write_text("nope")
        assert backup_service.list_backed_up_devices() == []


# ---------------------------------------------------------------------------
# BackupService.backup_via_parser
# ---------------------------------------------------------------------------


class TestBackupViaParser:
    def test_delegates_to_parser(
        self, backup_service: BackupService, fake_sd: Path, backup_root: Path
    ) -> None:
        dest = backup_root / "SN12345"
        manifest = RawFileManifest(
            device_files=[dest / "STR.edf"],
            nights={date(2025, 8, 7): [dest / "DATALOG" / "20250807" / "BRP.edf"]},
            source_root=dest,
        )
        parser = _make_mock_parser(backup_return=manifest)

        result = backup_service.backup_via_parser(parser, fake_sd, "SN12345")

        parser.backup_raw_data.assert_called_once()
        call_args = parser.backup_raw_data.call_args
        assert call_args[0] == (fake_sd, dest)  # positional args
        assert result.backup_root == dest
        assert not result.was_skipped
        assert result.manifest is manifest

    def test_skips_when_source_is_backup(
        self, backup_service: BackupService, backup_root: Path
    ) -> None:
        device_root = backup_root / "SN12345"
        device_root.mkdir(parents=True)
        parser = _make_mock_parser()

        result = backup_service.backup_via_parser(parser, device_root, "SN12345")

        parser.backup_raw_data.assert_not_called()
        assert result.was_skipped
        assert result.backup_root == device_root

    def test_skips_when_source_is_subdirectory_of_backup(
        self, backup_service: BackupService, backup_root: Path
    ) -> None:
        sub = backup_root / "SN12345" / "DATALOG" / "20250807"
        sub.mkdir(parents=True)
        parser = _make_mock_parser()

        result = backup_service.backup_via_parser(parser, sub, "SN12345")

        parser.backup_raw_data.assert_not_called()
        assert result.was_skipped

    def test_invalid_serial_raises(
        self, backup_service: BackupService, fake_sd: Path
    ) -> None:
        parser = _make_mock_parser()
        with pytest.raises(ValueError, match="invalid characters"):
            backup_service.backup_via_parser(parser, fake_sd, "../evil")

    def test_progress_callback_called(
        self, backup_service: BackupService, fake_sd: Path
    ) -> None:
        parser = _make_mock_parser()
        messages: list[str] = []

        backup_service.backup_via_parser(
            parser, fake_sd, "SN12345", progress_callback=messages.append
        )

        assert any("Backing up" in m for m in messages)


# ---------------------------------------------------------------------------
# RawFileManifest
# ---------------------------------------------------------------------------


class TestRawFileManifest:
    def test_total_files_empty(self) -> None:
        m = RawFileManifest()
        assert m.total_files == 0

    def test_total_files_counts_all(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.edf"
        f2 = tmp_path / "b.edf"
        f3 = tmp_path / "c.edf"
        for f in (f1, f2, f3):
            f.write_bytes(b"x")

        m = RawFileManifest(
            device_files=[f1],
            nights={date(2025, 1, 1): [f2, f3]},
            source_root=tmp_path,
        )
        assert m.total_files == 3

    def test_total_bytes(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.edf"
        f1.write_bytes(b"x" * 100)
        m = RawFileManifest(device_files=[f1], source_root=tmp_path)
        assert m.total_bytes == 100


# ---------------------------------------------------------------------------
# ResmedEDFParser backup methods (integration-level, using real files)
# ---------------------------------------------------------------------------


class TestResmedBackupRawData:
    def test_copies_files_to_dest(self, fake_sd: Path, tmp_path: Path) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        dest = tmp_path / "backup"

        manifest = parser.backup_raw_data(fake_sd, dest)

        assert (dest / "STR.edf").exists()
        assert (dest / "Identification.json").exists()
        assert manifest.source_root == dest
        assert len(manifest.device_files) >= 2
        assert len(manifest.nights) == 1

    def test_skips_existing_files(self, fake_sd: Path, tmp_path: Path) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        dest = tmp_path / "backup"

        parser.backup_raw_data(fake_sd, dest)

        manifest2 = parser.backup_raw_data(fake_sd, dest)

        assert (dest / "STR.edf").exists()
        assert manifest2.source_root == dest

    def test_str_versioning(self, fake_sd: Path, tmp_path: Path) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        dest = tmp_path / "backup"

        # First backup
        parser.backup_raw_data(fake_sd, dest)

        # Modify source STR.edf to trigger versioning
        (fake_sd / "STR.edf").write_bytes(b"\x00" * 512)

        # Second backup should archive old STR.edf
        parser.backup_raw_data(fake_sd, dest)

        str_backup = dest / "STR_Backup"
        assert str_backup.is_dir()
        snapshots = list(str_backup.glob("STR-*.edf"))
        assert len(snapshots) >= 1
        # The literal "STR-unknown.edf" must never appear; unparseable files get a hash suffix.
        assert not any(s.name == "STR-unknown.edf" for s in snapshots)

    def test_two_distinct_unparseable_strs_produce_two_snapshots(
        self, fake_sd: Path, tmp_path: Path
    ) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        dest = tmp_path / "backup"

        # First backup: source is b"\x00" * 256 (unparseable header), no dest yet → no snapshot.
        parser.backup_raw_data(fake_sd, dest)

        # Replace source with distinct unparseable content and back up; archives the null-byte dest.
        (fake_sd / "STR.edf").write_bytes(b"\xff" * 256)
        parser.backup_raw_data(fake_sd, dest)

        # Replace source again with yet another distinct unparseable file; archives the ff-byte dest.
        (fake_sd / "STR.edf").write_bytes(b"\xee" * 256)
        parser.backup_raw_data(fake_sd, dest)

        str_backup = dest / "STR_Backup"
        snapshots = list(str_backup.glob("STR-unknown-*.edf"))
        assert len(snapshots) == 2
        # Each snapshot must have a unique name (different hash suffixes).
        names = {s.name for s in snapshots}
        assert len(names) == 2

    def test_same_unparseable_str_reimport_idempotent(
        self, fake_sd: Path, tmp_path: Path
    ) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        dest = tmp_path / "backup"

        # Use a fixed unparseable content for all three backups.
        unparseable = b"\xff" * 256
        (fake_sd / "STR.edf").write_bytes(unparseable)

        # First backup: dest doesn't exist → just copies, no snapshot.
        parser.backup_raw_data(fake_sd, dest)

        # Second backup: dest (ff bytes) exists → snapshot it under STR-unknown-<hash>.edf.
        parser.backup_raw_data(fake_sd, dest)

        # Third backup: same content → snapshot name already exists → skipped.
        parser.backup_raw_data(fake_sd, dest)

        str_backup = dest / "STR_Backup"
        snapshots = list(str_backup.glob("STR-unknown-*.edf"))
        assert len(snapshots) == 1

    def test_valid_header_still_yields_dated_snapshot_name(
        self, tmp_path: Path
    ) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        sd = tmp_path / "sd"
        sd.mkdir()
        (sd / "Identification.json").write_text('{"serial": "DEF456"}')

        # Build a minimal 256-byte EDF header with parseable date/time fields.
        # Offsets 168-176: dd.mm.yy  → 15.03.24  (March 15, 2024)
        # Offsets 176-184: hh.mm.ss  → 14.30.00  (14:30:00)
        header = bytearray(256)
        header[168:176] = b"15.03.24"
        header[176:184] = b"14.30.00"
        (sd / "STR.edf").write_bytes(bytes(header))

        dest = tmp_path / "backup"
        # First backup: no dest → just copies, no snapshot yet.
        parser.backup_raw_data(sd, dest)

        # Second backup with a different source triggers a snapshot of the valid-header dest.
        altered = bytearray(header)
        altered[0] = 0xFF
        (sd / "STR.edf").write_bytes(bytes(altered))
        parser.backup_raw_data(sd, dest)

        str_backup = dest / "STR_Backup"
        snapshots = list(str_backup.glob("STR-*.edf"))
        assert len(snapshots) == 1
        assert snapshots[0].name == "STR-20240315-143000.edf"


class TestResmedGetRawFileManifest:
    def test_returns_all_files(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        manifest = parser.get_raw_file_manifest(fake_sd)

        assert any(f.name == "STR.edf" for f in manifest.device_files)
        assert len(manifest.nights) == 1
        assert manifest.source_root == fake_sd

    def test_date_filter_excludes(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        # Filter to a date range that excludes 2025-08-07
        manifest = parser.get_raw_file_manifest(
            fake_sd, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
        )

        assert len(manifest.nights) == 0
        # Device files are always included
        assert len(manifest.device_files) > 0

    def test_date_filter_includes(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser = ResmedEDFParser()
        # Night date is 2025-08-06 (1:34 AM session → previous night)
        manifest = parser.get_raw_file_manifest(
            fake_sd, date_from=date(2025, 8, 1), date_to=date(2025, 8, 31)
        )

        assert len(manifest.nights) == 1


# ---------------------------------------------------------------------------
# resmed_file_index module
# ---------------------------------------------------------------------------


class TestResmedFileIndex:
    def test_is_resmed_root(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_file_index import is_resmed_root

        assert is_resmed_root(fake_sd)
        assert not is_resmed_root(fake_sd / "DATALOG")

    def test_get_night_date_evening(self) -> None:
        from snore.parsers.resmed_file_index import get_night_date

        assert get_night_date(datetime(2025, 8, 7, 22, 30)) == "20250807"

    def test_get_night_date_after_midnight(self) -> None:
        from snore.parsers.resmed_file_index import get_night_date

        assert get_night_date(datetime(2025, 8, 8, 3, 0)) == "20250807"

    def test_scan_edf_files(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_file_index import scan_edf_files

        files = scan_edf_files(fake_sd / "DATALOG")
        assert len(files) == 2
        names = {f.name for f in files}
        assert "20250807_013454_BRP.edf" in names
        assert "20250807_013454_PLD.edf" in names

    def test_group_session_files(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_file_index import group_session_files

        groups = group_session_files(fake_sd / "DATALOG")
        # 01:34 AM → before noon → belongs to previous night (20250806)
        assert "20250806" in groups
        assert "20250807_013454" in groups["20250806"]
        assert "BRP" in groups["20250806"]["20250807_013454"]

    def test_flatten_night_files(self, fake_sd: Path) -> None:
        from snore.parsers.resmed_file_index import (
            flatten_night_files,
            group_session_files,
        )

        grouped = group_session_files(fake_sd / "DATALOG")
        flat = flatten_night_files(grouped)
        assert "20250806" in flat
        assert len(flat["20250806"]) == 2
