"""Unit tests for ResmedEDFParser._load_str_caches — STR_Backup merging."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from snore.parsers.resmed_edf import (
    ResmedEDFParser,
    _chain_therapy_days,
    _slice_str_cache,
)

StrCache = dict[date, dict[str, float]]


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
        primary_file = root / "STR.edf"

        d1 = date(2025, 1, 1)
        primary_settings = {d1: {"mode": 1.0, "pressure_min": 4.0}}
        primary_summaries = {d1: {"ahi": 1.5}}

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == primary_file:
                return (d1, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == primary_file:
                return primary_settings, primary_summaries
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
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

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                return (d_backup, 1)
            if path == primary_file:
                return (d_primary, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == backup_file:
                return {d_backup: {"mode": 1.0, "pressure_min": 4.0}}, None
            if path == primary_file:
                return {d_primary: {"mode": 1.0, "pressure_min": 6.0}}, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        assert d_backup in settings
        assert d_primary in settings

    def test_primary_wins_on_overlapping_dates(self, parser, tmp_path):
        """When backup and primary share a start-date and equal record count, primary wins."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        shared_date = date(2025, 1, 1)

        def header_effect(path: Path) -> tuple[date, int] | None:
            # Same start-date, same count — later file (primary) wins via >= tie-break.
            if path in (backup_file, primary_file):
                return (shared_date, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == primary_file:
                return {shared_date: {"mode": 1.0, "pressure_min": 8.0}}, None
            if path == backup_file:
                return {shared_date: {"mode": 1.0, "pressure_min": 4.0}}, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        # Primary (8.0) must win over backup (4.0) via later-file-wins tie-break.
        assert settings[shared_date]["pressure_min"] == 8.0

    def test_backup_only_when_primary_missing(self, parser, tmp_path):
        """Backup data is returned even when STR.edf does not exist."""
        root = _make_stub_dir(tmp_path, has_primary=False, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"

        d = date(2024, 11, 5)
        backup_data = {d: {"mode": 1.0, "pressure_min": 5.0}}

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                return (d, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == backup_file:
                return backup_data, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
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
        """When two backups share a start-date with equal record count, the later-sorted one wins."""
        root = _make_stub_dir(
            tmp_path,
            has_primary=False,
            backup_names=["STR-002.edf", "STR-001.edf"],
        )
        backup_001 = root / "STR_Backup" / "STR-001.edf"
        backup_002 = root / "STR_Backup" / "STR-002.edf"

        shared = date(2024, 12, 15)

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path in (backup_001, backup_002):
                return (shared, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == backup_001:
                return {shared: {"pressure_min": 4.0}}, None
            if path == backup_002:
                return {shared: {"pressure_min": 6.0}}, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        # Both have same start-date and record count; sorted order has 001 < 002
        # (alphabetical), so 002 has higher index and wins via later-file tie-break.
        assert settings is not None
        assert settings[shared]["pressure_min"] == 6.0

    def test_summaries_also_merged(self, parser, tmp_path):
        """Summaries from backup and primary are both merged."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        d_backup = date(2024, 12, 1)
        d_primary = date(2025, 1, 1)

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                return (d_backup, 1)
            if path == primary_file:
                return (d_primary, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == backup_file:
                return {d_backup: {"mode": 1.0}}, {d_backup: {"ahi": 2.0}}
            if path == primary_file:
                return {d_primary: {"mode": 1.0}}, {d_primary: {"ahi": 1.0}}
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            _, summaries = parser._load_str_caches(root)

        assert summaries is not None
        assert d_backup in summaries
        assert d_primary in summaries


class TestLongerFileWins:
    """F12: longer-file-wins per start-date (OSCAR :896-908, :980-993)."""

    def test_longer_file_wins_same_start_date(self, parser, tmp_path):
        """When two files share the same start-date, the one with more records wins."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        start = date(2025, 2, 21)

        # Backup: 3 days from start
        backup_data = {
            start: {"pressure_min": 4.0},
            date(2025, 2, 22): {"pressure_min": 4.0},
            date(2025, 2, 23): {"pressure_min": 4.0},
        }
        # Primary: 5 days from same start — longer, should win
        primary_data = {
            start: {"pressure_min": 8.0},
            date(2025, 2, 22): {"pressure_min": 8.0},
            date(2025, 2, 23): {"pressure_min": 8.0},
            date(2025, 2, 24): {"pressure_min": 8.0},
            date(2025, 2, 25): {"pressure_min": 8.0},
        }

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                return (start, 3)
            if path == primary_file:
                return (start, 5)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == primary_file:
                return primary_data, None
            if path == backup_file:
                return backup_data, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        # Primary is longer (5 > 3) → all dates come from primary
        assert settings[start]["pressure_min"] == 8.0
        assert date(2025, 2, 24) in settings  # primary-only day present
        assert date(2025, 2, 25) in settings  # primary-only day present

    def test_shorter_file_excluded_same_start_date(self, parser, tmp_path):
        """When primary is longer, backup data from overlapping dates is NOT used."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        start = date(2025, 2, 21)
        shorter = {
            start: {"pressure_min": 4.0},
            date(2025, 2, 22): {"pressure_min": 4.0},
        }
        longer = {
            start: {"pressure_min": 9.0},
            date(2025, 2, 22): {"pressure_min": 9.0},
            date(2025, 2, 23): {"pressure_min": 9.0},
        }

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                return (start, 2)
            if path == primary_file:
                return (start, 3)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == primary_file:
                return longer, None
            if path == backup_file:
                return shorter, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        # backup value must NOT appear
        assert settings[start]["pressure_min"] == 9.0

    def test_different_start_dates_both_included(self, parser, tmp_path):
        """Files with different start-dates never compete — both contribute their ranges."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        d_early = date(2023, 8, 22)
        d_late = date(2025, 2, 21)

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                return (d_early, 1)
            if path == primary_file:
                return (d_late, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == backup_file:
                return {d_early: {"pressure_min": 4.0}}, None
            if path == primary_file:
                return {d_late: {"pressure_min": 8.0}}, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        assert d_early in settings  # from backup
        assert d_late in settings  # from primary

    def test_equal_length_later_file_wins(self, parser, tmp_path):
        """When two files have the same start-date and record count, the later-processed one wins."""
        root = _make_stub_dir(
            tmp_path, has_primary=False, backup_names=["STR-001.edf", "STR-002.edf"]
        )
        backup_001 = root / "STR_Backup" / "STR-001.edf"
        backup_002 = root / "STR_Backup" / "STR-002.edf"

        start = date(2024, 8, 22)

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path in (backup_001, backup_002):
                return (start, 1)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == backup_001:
                return {start: {"pressure_min": 3.0}}, None
            if path == backup_002:
                return {start: {"pressure_min": 5.0}}, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        assert settings is not None
        # Both files have len=1; >= comparison means later-processed (002) wins.
        assert settings[start]["pressure_min"] == 5.0


class TestCorruptWinnerFallback:
    """F12b: corrupt winner falls back to next-best candidate for same start-date."""

    def test_corrupt_winner_falls_back_to_shorter_healthy_file(self, parser, tmp_path):
        """Longer file selected as winner but full load returns None → shorter healthy file used."""
        root = _make_stub_dir(tmp_path, has_primary=True, backup_names=["STR-001.edf"])
        backup_file = root / "STR_Backup" / "STR-001.edf"
        primary_file = root / "STR.edf"

        start = date(2025, 3, 10)
        healthy_data = {
            start: {"mode": 1.0, "pressure_min": 7.0},
            date(2025, 3, 11): {"mode": 1.0, "pressure_min": 7.0},
            date(2025, 3, 12): {"mode": 1.0, "pressure_min": 7.0},
        }

        def header_effect(path: Path) -> tuple[date, int] | None:
            if path == backup_file:
                # Shorter file (3 records) — healthy
                return (start, 3)
            if path == primary_file:
                # Longer file (5 records) — selected as winner but corrupt
                return (start, 5)
            return None

        def file_effect(path: Path) -> tuple[StrCache | None, StrCache | None]:
            if path == primary_file:
                # Corrupt: full load fails → returns (None, None)
                return None, None
            if path == backup_file:
                return healthy_data, None
            return None, None

        with (
            patch.object(parser, "_read_str_file_header", side_effect=header_effect),
            patch.object(parser, "_preload_str_file", side_effect=file_effect),
        ):
            settings, _ = parser._load_str_caches(root)

        # Corrupt winner returned None → fallback to shorter healthy backup.
        assert settings is not None
        assert settings[start]["pressure_min"] == 7.0
        assert date(2025, 3, 11) in settings
        assert date(2025, 3, 12) in settings


class TestSliceStrCache:
    """Tests for the STR-cache slicing helpers used to limit pickle payload per future.

    Segments map to therapy days via: therapy_day = (seg_start - 12h).date().
    A segment id "YYYYMMDD_220000" maps to the same calendar date (22:00 - 12h = 10:00).
    """

    def test_returns_entry_for_matching_segment_therapy_day(self):
        d = date(2025, 1, 1)
        other = date(2025, 1, 2)
        cache = {d: {"pressure_min": 4.0}, other: {"pressure_min": 6.0}}
        # "20250101_220000" - 12h → 2025-01-01 10:00 → therapy_day = d
        segments = {"20250101_220000": {"BRP": Path("/fake.edf")}}
        result = _slice_str_cache(cache, _chain_therapy_days(segments))
        assert result == {d: {"pressure_min": 4.0}}

    def test_returns_none_when_entry_absent(self):
        cache = {date(2025, 1, 2): {"pressure_min": 6.0}}
        # therapy_day for "20250101_220000" = date(2025, 1, 1) — not in cache
        segments = {"20250101_220000": {"BRP": Path("/fake.edf")}}
        result = _slice_str_cache(cache, _chain_therapy_days(segments))
        assert result is None

    def test_returns_none_for_none_cache(self):
        segments = {"20250101_220000": {}}
        assert _slice_str_cache(None, _chain_therapy_days(segments)) is None

    def test_result_does_not_include_other_dates(self):
        d = date(2025, 6, 15)
        cache = {
            date(2025, 6, 14): {"pressure_min": 4.0},
            d: {"pressure_min": 8.0},
            date(2025, 6, 16): {"pressure_min": 5.0},
        }
        # "20250615_220000" - 12h → 2025-06-15 10:00 → therapy_day = d
        segments = {"20250615_220000": {"BRP": Path("/fake.edf")}}
        result = _slice_str_cache(cache, _chain_therapy_days(segments))
        assert result is not None
        assert list(result.keys()) == [d]

    def test_cross_noon_chain_returns_two_entries(self):
        """A chain spanning noon (noon-rollover) covers two therapy days."""
        d1 = date(2026, 3, 20)
        d2 = date(2026, 3, 21)
        cache = {d1: {"ahi": 1.0}, d2: {"ahi": 2.0}}
        # Pre-noon segment: "20260321_020000" - 12h → 2026-03-20 14:00 → d1
        # Post-noon segment: "20260321_140000" - 12h → 2026-03-21 02:00 → d2
        segments = {
            "20260321_020000": {"BRP": Path("/a.edf")},
            "20260321_140000": {"BRP": Path("/b.edf")},
        }
        result = _slice_str_cache(cache, _chain_therapy_days(segments))
        assert result == {d1: {"ahi": 1.0}, d2: {"ahi": 2.0}}
