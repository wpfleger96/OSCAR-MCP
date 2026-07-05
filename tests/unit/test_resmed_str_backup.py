"""Unit tests for ResmedEDFParser._load_str_caches — STR_Backup merging."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from snore.parsers.resmed_edf import ResmedEDFParser


@pytest.fixture
def parser():
    return ResmedEDFParser()


def _make_stub_dir(
    tmp_path: Path, has_primary: bool = True, backup_names: list[str] | None = None
) -> Path:
    """Create a minimal directory tree with stub STR files."""
    root = tmp_path / "device"
    root.mkdir()
    if has_primary:
        (root / "STR.edf").touch()
    if backup_names:
        backup_dir = root / "STR_Backup"
        backup_dir.mkdir()
        for name in backup_names:
            (backup_dir / name).touch()
    return root


class TestLoadStrCaches:
    """Tests for _load_str_caches merging logic."""

    def test_primary_only_when_no_backup_dir(self, parser, tmp_path):
        """With no STR_Backup directory, result comes entirely from STR.edf."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=None)
        d1 = date(2025, 1, 1)
        primary_settings = {d1: {"mode": 1.0, "pressure_min": 4.0}}
        primary_summaries = {d1: {"ahi": 1.5}}

        with (
            patch.object(
                parser, "_preload_str_settings", return_value=primary_settings
            ),
            patch.object(
                parser, "_preload_str_summaries", return_value=primary_summaries
            ),
        ):
            settings, summaries = parser._load_str_caches(root)

        assert settings == primary_settings
        assert summaries == primary_summaries

    def test_backup_dates_merged(self, parser, tmp_path):
        """Dates present only in backup files appear in the merged result."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        d_backup = date(2024, 12, 1)
        d_primary = date(2025, 1, 1)

        def settings_side_effect(path: Path) -> dict:
            if path == backup_file:
                return {d_backup: {"mode": 1.0, "pressure_min": 4.0}}
            if path == primary_file:
                return {d_primary: {"mode": 1.0, "pressure_min": 6.0}}
            return {}

        with (
            patch.object(
                parser, "_preload_str_settings", side_effect=settings_side_effect
            ),
            patch.object(parser, "_preload_str_summaries", return_value=None),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        assert d_backup in settings
        assert d_primary in settings

    def test_primary_wins_on_overlapping_dates(self, parser, tmp_path):
        """When backup and primary share a date, primary values overwrite backup values."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        shared_date = date(2025, 1, 1)

        def settings_side_effect(path: Path) -> dict:
            if path == backup_file:
                return {shared_date: {"mode": 1.0, "pressure_min": 4.0}}
            if path == primary_file:
                return {shared_date: {"mode": 1.0, "pressure_min": 8.0}}
            return {}

        with (
            patch.object(
                parser, "_preload_str_settings", side_effect=settings_side_effect
            ),
            patch.object(parser, "_preload_str_summaries", return_value=None),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        # Primary (8.0) must win over backup (4.0).
        assert settings[shared_date]["pressure_min"] == 8.0

    def test_backup_only_when_primary_missing(self, parser, tmp_path):
        """Backup data is returned even when STR.edf does not exist."""
        root = _make_stub_dir(tmp_path, has_primary=False, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"

        d = date(2024, 11, 5)
        backup_data = {d: {"mode": 1.0, "pressure_min": 5.0}}

        def settings_side_effect(path: Path) -> dict:
            if path == backup_file:
                return backup_data
            return {}

        with (
            patch.object(
                parser, "_preload_str_settings", side_effect=settings_side_effect
            ),
            patch.object(parser, "_preload_str_summaries", return_value=None),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        assert d in settings

    def test_no_files_returns_none_none(self, parser, tmp_path):
        """No STR.edf and no STR_Backup → (None, None)."""
        root = _make_stub_dir(tmp_path, has_primary=False, backup_names=None)
        settings, summaries = parser._load_str_caches(root)
        assert settings is None
        assert summaries is None

    def test_multiple_backup_files_sorted_order(self, parser, tmp_path):
        """Multiple backup files are processed in sorted (chronological) order."""
        root = _make_stub_dir(
            tmp_path,
            has_primary=False,
            backup_names=["STR-002.edf", "STR-001.edf"],
        )
        backup_001 = root / "STR_Backup" / "STR-001.edf"
        backup_002 = root / "STR_Backup" / "STR-002.edf"

        shared = date(2024, 12, 15)

        call_order: list[Path] = []

        def settings_side_effect(path: Path) -> dict:
            call_order.append(path)
            if path == backup_001:
                return {shared: {"pressure_min": 4.0}}
            if path == backup_002:
                return {shared: {"pressure_min": 6.0}}
            return {}

        with (
            patch.object(
                parser, "_preload_str_settings", side_effect=settings_side_effect
            ),
            patch.object(parser, "_preload_str_summaries", return_value=None),
        ):
            settings, _ = parser._load_str_caches(root)

        # STR-001 before STR-002 in sorted order; STR-002 overwrites STR-001.
        assert call_order[0] == backup_001
        assert call_order[1] == backup_002
        assert settings is not None
        assert settings[shared]["pressure_min"] == 6.0

    def test_summaries_also_merged(self, parser, tmp_path):
        """Summaries from backup and primary are both merged."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        d_backup = date(2024, 12, 1)
        d_primary = date(2025, 1, 1)

        def summaries_side_effect(path: Path) -> dict | None:
            if path == backup_file:
                return {d_backup: {"ahi": 2.0}}
            if path == primary_file:
                return {d_primary: {"ahi": 1.0}}
            return None

        with (
            patch.object(parser, "_preload_str_settings", return_value=None),
            patch.object(
                parser, "_preload_str_summaries", side_effect=summaries_side_effect
            ),
        ):
            _, summaries = parser._load_str_caches(root)

        assert summaries is not None
        assert d_backup in summaries
        assert d_primary in summaries
