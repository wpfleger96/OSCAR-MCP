"""Unit tests for ImportService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from snore.services.import_service import ImportService, safe_relative_path


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
        assert safe_relative_path("test.edf") == "test.edf"

    def test_posix_path_preserved(self):
        assert safe_relative_path("SDCARD/DATALOG/x.edf") == "SDCARD/DATALOG/x.edf"

    def test_backslash_path_converted(self):
        assert safe_relative_path("SDCARD\\DATALOG\\x.edf") == "SDCARD/DATALOG/x.edf"

    def test_path_traversal_stripped(self):
        assert safe_relative_path("../../etc/passwd") == "etc/passwd"

    def test_absolute_posix_path(self):
        assert safe_relative_path("/etc/passwd") == "etc/passwd"

    def test_empty_string_returns_none(self):
        assert safe_relative_path("") is None

    def test_only_dots_returns_none(self):
        assert safe_relative_path("../..") is None

    def test_windows_drive_letter_stripped(self):
        assert safe_relative_path("C:/foo/bar.edf") == "foo/bar.edf"

    def test_nul_bytes_removed(self):
        assert safe_relative_path("DATALOG/bad\x00name.edf") == "DATALOG/badname.edf"

    def test_only_nul_bytes_returns_none(self):
        assert safe_relative_path("\x00") is None
