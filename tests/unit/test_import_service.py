"""Unit tests for ImportService."""

from __future__ import annotations

import io
import os

from unittest.mock import MagicMock, patch

from snore.services.import_service import ImportService, _safe_relative_path
from snore.services.schemas import ImportResult


class TestDetectSources:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        service = ImportService()
        with patch("snore.services.import_service.register_all_parsers"):
            with patch(
                "snore.services.import_service.parser_registry.detect_all_parsers",
                return_value=[],
            ):
                result = service.detect_sources(tmp_path)
        assert result == []

    def test_single_root_detection(self, tmp_path):
        mock_parser = MagicMock()
        mock_parser.parser_id = "resmed"
        mock_detection = MagicMock()
        mock_detection.metadata = {
            "device_serial": "12345",
            "profile_name": "default",
            "structure_type": "resmed_sd",
            "data_root": str(tmp_path / "DATALOG"),
        }

        service = ImportService()
        with patch("snore.services.import_service.register_all_parsers"):
            with patch(
                "snore.services.import_service.parser_registry.detect_all_parsers",
                return_value=[(mock_parser, mock_detection)],
            ):
                result = service.detect_sources(tmp_path)

        assert len(result) == 1
        assert result[0].parser_name == "resmed"
        assert result[0].device_serial == "12345"

    def test_multi_root_detection(self, tmp_path):
        mock_parser = MagicMock()
        mock_parser.parser_id = "resmed"
        mock_detection = MagicMock()
        mock_detection.metadata = {
            "all_roots": ["/root1", "/root2"],
            "root_metadata": {
                "/root1": {"device_serial": "AAA"},
                "/root2": {"device_serial": "BBB"},
            },
        }

        service = ImportService()
        with patch("snore.services.import_service.register_all_parsers"):
            with patch(
                "snore.services.import_service.parser_registry.detect_all_parsers",
                return_value=[(mock_parser, mock_detection)],
            ):
                result = service.detect_sources(tmp_path)

        assert len(result) == 2
        assert result[0].device_serial == "AAA"
        assert result[1].device_serial == "BBB"


class TestSafeRelativePath:
    def test_simple_name_unchanged(self):
        assert _safe_relative_path("test.edf") == "test.edf"

    def test_posix_path_preserved(self):
        assert _safe_relative_path("SDCARD/DATALOG/x.edf") == "SDCARD/DATALOG/x.edf"

    def test_backslash_path_converted(self):
        assert _safe_relative_path("SDCARD\\DATALOG\\x.edf") == "SDCARD/DATALOG/x.edf"

    def test_path_traversal_stripped(self):
        assert _safe_relative_path("../../etc/passwd") == "etc/passwd"

    def test_absolute_posix_path(self):
        assert _safe_relative_path("/etc/passwd") == "etc/passwd"

    def test_empty_string_returns_none(self):
        assert _safe_relative_path("") is None

    def test_only_dots_returns_none(self):
        assert _safe_relative_path("../..") is None

    def test_windows_drive_letter_stripped(self):
        assert _safe_relative_path("C:/foo/bar.edf") == "foo/bar.edf"

    def test_nul_bytes_removed(self):
        assert _safe_relative_path("DATALOG/bad\x00name.edf") == "DATALOG/badname.edf"

    def test_only_nul_bytes_returns_none(self):
        assert _safe_relative_path("\x00") is None


class TestImportFromUpload:
    def test_path_traversal_filename_contained(self):
        """../../etc/passwd is sanitized to etc/passwd and written under tmpdir."""
        service = ImportService()
        # Check file existence inside fake_detect while the tmpdir is still live.
        file_check: list[bool] = []

        def fake_detect(path):
            file_check.append((path / "etc" / "passwd").exists())
            return []

        def fake_import(sources, **kwargs):
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(
                service, "import_sources", side_effect=fake_import
            ) as mock_import,
        ):
            service.import_from_upload([("../../etc/passwd", io.BytesIO(b"data"))])

        mock_import.assert_called_once()
        assert mock_import.call_args[1]["backup"] is False
        assert file_check == [True]

    def test_empty_filename_becomes_unknown(self):
        service = ImportService()
        written_files: list = []

        def fake_detect(path):
            written_files.extend(list(path.rglob("*")))
            return []

        def fake_import(sources, **kwargs):
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(service, "import_sources", side_effect=fake_import),
        ):
            service.import_from_upload([("", io.BytesIO(b"data"))])

        assert any(f.name == "unknown" for f in written_files)

    def test_temp_dir_cleaned_up(self):
        service = ImportService()
        captured_paths: list = []

        def fake_detect(path):
            captured_paths.append(str(path))
            return []

        def fake_import(sources, **kwargs):
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(service, "import_sources", side_effect=fake_import),
        ):
            service.import_from_upload([("test.edf", io.BytesIO(b"data"))])

        assert len(captured_paths) == 1
        assert not os.path.exists(captured_paths[0])

    def test_backup_disabled_for_uploads(self):
        service = ImportService()

        def fake_detect(path):
            return []

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(service, "import_sources") as mock_import,
        ):
            mock_import.return_value = ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )
            service.import_from_upload([("test.edf", io.BytesIO(b"data"))])

        mock_import.assert_called_once()
        assert mock_import.call_args[1]["backup"] is False

    def test_nested_relative_path_preserved(self):
        """Nested filenames are written at the correct relative path under tmpdir."""
        service = ImportService()
        file_check: list[bool] = []

        def fake_detect(path):
            file_check.append((path / "SDCARD" / "DATALOG" / "test.edf").exists())
            return []

        def fake_import(sources, **kwargs):
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(service, "import_sources", side_effect=fake_import),
        ):
            service.import_from_upload([("SDCARD/DATALOG/test.edf", io.BytesIO(b"x"))])

        assert file_check == [True]

    def test_duplicate_sanitized_paths_no_crash(self):
        """Duplicate sanitized paths do not crash — last write wins."""
        service = ImportService()

        def fake_detect(path):
            return []

        def fake_import(sources, **kwargs):
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(service, "import_sources", side_effect=fake_import),
        ):
            service.import_from_upload(
                [
                    ("test.edf", io.BytesIO(b"first")),
                    ("test.edf", io.BytesIO(b"second")),
                ]
            )

    def test_backslash_path_creates_directory_structure(self):
        """Windows-style backslash paths produce the correct directory structure."""
        service = ImportService()
        file_check: list[bool] = []

        def fake_detect(path):
            file_check.append((path / "SDCARD" / "DATALOG" / "test.edf").exists())
            return []

        def fake_import(sources, **kwargs):
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with (
            patch.object(service, "detect_sources", side_effect=fake_detect),
            patch.object(service, "import_sources", side_effect=fake_import),
        ):
            service.import_from_upload(
                [("SDCARD\\DATALOG\\test.edf", io.BytesIO(b"x"))]
            )

        assert file_check == [True]
