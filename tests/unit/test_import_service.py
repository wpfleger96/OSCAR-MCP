"""Unit tests for ImportService."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from snore.services.import_service import ImportService, safe_relative_path
from snore.services.schemas import ImportSource


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


class TestProfileTimezoneWiring:
    async def test_stored_profile_timezone_reaches_parser(self, tmp_path):
        """Profile.timezone loaded at import time is passed to parse_sessions."""
        mock_parser = MagicMock()
        mock_parser.parser_id = "oscar_binary"
        mock_parser.parse_sessions.return_value = iter([])

        fake_db = MagicMock()
        fake_db.get = AsyncMock(
            return_value=SimpleNamespace(timezone="America/New_York")
        )

        @asynccontextmanager
        async def fake_scope(**kwargs):
            yield fake_db

        source = ImportSource(parser_name="oscar_binary", root_path=str(tmp_path))
        service = ImportService()

        with (
            patch("snore.services.import_service.session_scope", fake_scope),
            patch(
                "snore.services.import_service.parser_registry.list_parsers",
                return_value=[mock_parser],
            ),
        ):
            await service.import_sources(
                [source], profile_id=42, dry_run=True, backup=False
            )

        fake_db.get.assert_awaited_once()
        assert fake_db.get.await_args.args[1] == 42
        mock_parser.parse_sessions.assert_called_once()
        call_kwargs = mock_parser.parse_sessions.call_args.kwargs
        assert call_kwargs["timezone_name"] == "America/New_York"

    async def test_missing_profile_passes_none(self, tmp_path):
        mock_parser = MagicMock()
        mock_parser.parser_id = "oscar_binary"
        mock_parser.parse_sessions.return_value = iter([])

        fake_db = MagicMock()
        fake_db.get = AsyncMock(return_value=None)

        @asynccontextmanager
        async def fake_scope(**kwargs):
            yield fake_db

        source = ImportSource(parser_name="oscar_binary", root_path=str(tmp_path))
        service = ImportService()

        with (
            patch("snore.services.import_service.session_scope", fake_scope),
            patch(
                "snore.services.import_service.parser_registry.list_parsers",
                return_value=[mock_parser],
            ),
        ):
            await service.import_sources(
                [source], profile_id=42, dry_run=True, backup=False
            )

        assert mock_parser.parse_sessions.call_args.kwargs["timezone_name"] is None

    async def test_corrupt_stored_timezone_raises_clean_runtime_error(self, tmp_path):
        """A corrupt Profile.timezone fails eagerly with an actionable RuntimeError.

        ZoneInfoNotFoundError is a KeyError subclass, so if it escaped from the
        lazy parse generators the CLI's `except RuntimeError` would miss it and
        the user would see a raw traceback.  import_sources must validate the
        stored name up front and re-raise as RuntimeError.
        """
        mock_parser = MagicMock()
        mock_parser.parser_id = "oscar_binary"

        fake_db = MagicMock()
        fake_db.get = AsyncMock(return_value=SimpleNamespace(timezone="Not/A_Zone"))

        @asynccontextmanager
        async def fake_scope(**kwargs):
            yield fake_db

        source = ImportSource(parser_name="oscar_binary", root_path=str(tmp_path))
        service = ImportService()

        with (
            patch("snore.services.import_service.session_scope", fake_scope),
            patch(
                "snore.services.import_service.parser_registry.list_parsers",
                return_value=[mock_parser],
            ),
        ):
            with pytest.raises(RuntimeError, match="snore profile set-timezone"):
                # Real (non-dry-run) import path — validation fires before any
                # parsing or DB writes.
                await service.import_sources(
                    [source], profile_id=42, dry_run=False, backup=False
                )

        mock_parser.parse_sessions.assert_not_called()


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
