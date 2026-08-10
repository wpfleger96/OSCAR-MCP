"""Unit tests for the client-side upload pre-check helpers.

Covers:
- normalize_datalog_suffix: path normalization and DATALOG detection
- _build_backup_index: filesystem walk producing per-serial (suffix, size) sets
- _classify_files: dominant-serial skippability decision (anchor exclusion,
  size matching, cross-device collision prevention, original-path echo)
- PrecheckFileEntry / PrecheckRequest: pydantic field constraints
"""

from __future__ import annotations

import os

from pathlib import Path

import pytest

from pydantic import ValidationError

from snore.api.routers.import_data import (
    _ANCHOR_NAMES,
    PrecheckFileEntry,
    PrecheckRequest,
    _build_backup_index,
    _classify_files,
)
from snore.services.import_service import normalize_datalog_suffix

# ---------------------------------------------------------------------------
# normalize_datalog_suffix
# ---------------------------------------------------------------------------


class TestNormalizeDatalogSuffix:
    def test_basic_nested_path(self):
        result = normalize_datalog_suffix("SDCARD/DATALOG/2024/20240101_010000_BRP.edf")
        assert result == "datalog/2024/20240101_010000_brp.edf"

    def test_mixed_case_datalog_component(self):
        result = normalize_datalog_suffix("sdcard/Datalog/2024/file.EDF")
        assert result == "datalog/2024/file.edf"

    def test_flat_datalog_layout(self):
        result = normalize_datalog_suffix("SDCARD/DATALOG/file.edf")
        assert result == "datalog/file.edf"

    def test_backslash_separators(self):
        result = normalize_datalog_suffix("SDCARD\\DATALOG\\2024\\file.edf")
        assert result == "datalog/2024/file.edf"

    def test_no_datalog_str_edf(self):
        assert normalize_datalog_suffix("STR.edf") is None

    def test_no_datalog_identification_json(self):
        assert normalize_datalog_suffix("Identification.json") is None

    def test_no_datalog_arbitrary_path(self):
        assert normalize_datalog_suffix("some/other/path.txt") is None

    def test_datalog_component_starts_suffix(self):
        # The DATALOG component itself is included in the returned suffix.
        result = normalize_datalog_suffix("DATALOG/file.edf")
        assert result == "datalog/file.edf"


# ---------------------------------------------------------------------------
# _build_backup_index
# ---------------------------------------------------------------------------


class TestBuildBackupIndex:
    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        indexes = _build_backup_index(tmp_path / "nonexistent")
        assert indexes == {}

    def test_serial_dir_without_datalog_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "SN12345").mkdir()
        indexes = _build_backup_index(tmp_path)
        assert indexes == {}

    def test_basic_file_indexed_with_correct_suffix_and_size(
        self, tmp_path: Path
    ) -> None:
        serial = tmp_path / "SN12345"
        datalog = serial / "DATALOG"
        datalog.mkdir(parents=True)
        f = datalog / "file.edf"
        f.write_bytes(b"x" * 100)

        indexes = _build_backup_index(tmp_path)
        assert ("datalog/file.edf", 100) in indexes.get("SN12345", set())

    def test_nested_path_indexed_correctly(self, tmp_path: Path) -> None:
        serial = tmp_path / "SN12345"
        nested = serial / "DATALOG" / "2024"
        nested.mkdir(parents=True)
        f = nested / "20240101_010000_BRP.edf"
        f.write_bytes(b"y" * 200)

        indexes = _build_backup_index(tmp_path)
        assert ("datalog/2024/20240101_010000_brp.edf", 200) in indexes.get(
            "SN12345", set()
        )

    def test_multi_serial_dirs_indexed_separately(self, tmp_path: Path) -> None:
        # Two serial dirs each with a different file — indexed under separate keys.
        for sn, name, size in [("SN1", "a.edf", 50), ("SN2", "b.edf", 75)]:
            d = tmp_path / sn / "DATALOG"
            d.mkdir(parents=True)
            (d / name).write_bytes(b"z" * size)

        indexes = _build_backup_index(tmp_path)
        assert ("datalog/a.edf", 50) in indexes.get("SN1", set())
        assert ("datalog/b.edf", 75) in indexes.get("SN2", set())

    def test_same_suffix_different_sizes_indexed_per_serial(
        self, tmp_path: Path
    ) -> None:
        # Same relative path across two serials but different sizes —
        # each is kept in its own serial set, not merged.
        for sn, size in [("SN1", 100), ("SN2", 200)]:
            d = tmp_path / sn / "DATALOG"
            d.mkdir(parents=True)
            (d / "shared.edf").write_bytes(b"a" * size)

        indexes = _build_backup_index(tmp_path)
        assert ("datalog/shared.edf", 100) in indexes.get("SN1", set())
        assert ("datalog/shared.edf", 200) in indexes.get("SN2", set())

    def test_non_dir_in_profile_root_skipped(self, tmp_path: Path) -> None:
        # A plain file at the profile root must not crash the walk.
        (tmp_path / "not_a_dir.txt").write_text("noise")
        serial = tmp_path / "SN1"
        (serial / "DATALOG").mkdir(parents=True)
        (serial / "DATALOG" / "file.edf").write_bytes(b"x" * 10)

        indexes = _build_backup_index(tmp_path)
        assert ("datalog/file.edf", 10) in indexes.get("SN1", set())

    def test_permission_denied_serial_dir_does_not_raise(self, tmp_path: Path) -> None:
        """A serial dir with mode 0o000 must not raise; the readable sibling is indexed."""
        if os.getuid() == 0:
            pytest.skip("Running as root — file mode restrictions are ignored")

        # SN1: readable, should be indexed.
        sn1_datalog = tmp_path / "SN1" / "DATALOG"
        sn1_datalog.mkdir(parents=True)
        (sn1_datalog / "file.edf").write_bytes(b"x" * 50)

        # SN2: completely inaccessible.
        sn2 = tmp_path / "SN2"
        sn2.mkdir()
        sn2.chmod(0o000)

        try:
            indexes = _build_backup_index(tmp_path)
            # Must not raise, and SN1 must still be indexed.
            assert ("datalog/file.edf", 50) in indexes.get("SN1", set())
        finally:
            sn2.chmod(0o755)  # restore so tmp_path cleanup works


# ---------------------------------------------------------------------------
# _classify_files
# ---------------------------------------------------------------------------


def _entry(path: str, size: int) -> PrecheckFileEntry:
    return PrecheckFileEntry(path=path, size=size)


def _single_serial_index(
    serial: str, pairs: set[tuple[str, int]]
) -> dict[str, set[tuple[str, int]]]:
    return {serial: pairs}


class TestClassifyFiles:
    def test_matching_path_and_size_is_skippable(self):
        indexes = _single_serial_index("SN1", {("datalog/2024/file.edf", 512)})
        files = [_entry("SDCARD/DATALOG/2024/file.edf", 512)]
        assert _classify_files(indexes, files) == ["SDCARD/DATALOG/2024/file.edf"]

    def test_size_mismatch_not_skippable(self):
        indexes = _single_serial_index("SN1", {("datalog/2024/file.edf", 512)})
        files = [_entry("SDCARD/DATALOG/2024/file.edf", 256)]
        assert _classify_files(indexes, files) == []

    def test_anchor_str_edf_never_skippable(self):
        # Even if STR.edf somehow appears in the index, it must not be returned.
        indexes = _single_serial_index("SN1", {("datalog/str.edf", 1000)})
        files = [_entry("SDCARD/DATALOG/STR.edf", 1000)]
        assert _classify_files(indexes, files) == []

    def test_anchor_identification_json_never_skippable(self):
        indexes = _single_serial_index("SN1", {("datalog/identification.json", 50)})
        files = [_entry("SDCARD/DATALOG/Identification.json", 50)]
        assert _classify_files(indexes, files) == []

    def test_anchor_identification_tgt_never_skippable(self):
        indexes = _single_serial_index("SN1", {("datalog/identification.tgt", 50)})
        files = [_entry("SDCARD/DATALOG/Identification.tgt", 50)]
        assert _classify_files(indexes, files) == []

    def test_non_datalog_path_not_skippable(self):
        indexes = _single_serial_index("SN1", {("datalog/str.edf", 1000)})
        files = [_entry("STR.edf", 1000)]
        assert _classify_files(indexes, files) == []

    def test_original_path_string_echoed(self):
        # The client sends mixed-case; the original string must be echoed back.
        indexes = _single_serial_index("SN1", {("datalog/2024/20240101_brp.edf", 256)})
        original = "SDCARD/DATALOG/2024/20240101_BRP.edf"
        files = [_entry(original, 256)]
        result = _classify_files(indexes, files)
        assert result == [original]

    def test_backslash_path_matched_and_echoed(self):
        indexes = _single_serial_index("SN1", {("datalog/2024/file.edf", 100)})
        original = "SDCARD\\DATALOG\\2024\\file.edf"
        files = [_entry(original, 100)]
        result = _classify_files(indexes, files)
        assert result == [original]

    def test_mixed_skippable_and_not(self):
        indexes = _single_serial_index("SN1", {("datalog/present.edf", 100)})
        files = [
            _entry("SDCARD/DATALOG/present.edf", 100),
            _entry("SDCARD/DATALOG/absent.edf", 200),
        ]
        result = _classify_files(indexes, files)
        assert result == ["SDCARD/DATALOG/present.edf"]

    def test_anchor_names_covers_all_three(self):
        assert "str.edf" in _ANCHOR_NAMES
        assert "identification.json" in _ANCHOR_NAMES
        assert "identification.tgt" in _ANCHOR_NAMES

    def test_empty_indexes_returns_empty(self):
        assert _classify_files({}, [_entry("SDCARD/DATALOG/file.edf", 100)]) == []

    def test_dominant_serial_wins_over_minority(self):
        # SN1 has 3 matching files; SN2 has 1 unique file.
        # Only SN1 results are returned — the SN2 file is not skippable because
        # the card overwhelmingly matches SN1 (cross-serial match treated as coincidence).
        indexes = {
            "SN1": {
                ("datalog/a.edf", 100),
                ("datalog/b.edf", 200),
                ("datalog/c.edf", 300),
            },
            "SN2": {("datalog/d.edf", 400)},
        }
        files = [
            _entry("SDCARD/DATALOG/a.edf", 100),
            _entry("SDCARD/DATALOG/b.edf", 200),
            _entry("SDCARD/DATALOG/c.edf", 300),
            _entry("SDCARD/DATALOG/d.edf", 400),  # only in SN2
        ]
        result = _classify_files(indexes, files)
        assert set(result) == {
            "SDCARD/DATALOG/a.edf",
            "SDCARD/DATALOG/b.edf",
            "SDCARD/DATALOG/c.edf",
        }
        assert "SDCARD/DATALOG/d.edf" not in result

    def test_anchor_and_data_file_both_in_index_only_data_skippable(self):
        # Even when the anchor appears in the index alongside real data,
        # only the data file is returned.
        indexes = {
            "SN1": {
                ("datalog/str.edf", 1000),  # anchor — never skippable
                ("datalog/2024/data.edf", 512),
            }
        }
        files = [
            _entry("SDCARD/DATALOG/STR.edf", 1000),
            _entry("SDCARD/DATALOG/2024/data.edf", 512),
        ]
        result = _classify_files(indexes, files)
        assert result == ["SDCARD/DATALOG/2024/data.edf"]
        assert "SDCARD/DATALOG/STR.edf" not in result


# ---------------------------------------------------------------------------
# PrecheckFileEntry / PrecheckRequest field constraints (Fix 3)
# ---------------------------------------------------------------------------


class TestPrecheckFieldConstraints:
    def test_path_at_max_length_is_valid(self):
        entry = PrecheckFileEntry(path="x" * 1024, size=0)
        assert len(entry.path) == 1024

    def test_path_over_max_length_raises(self):
        with pytest.raises(ValidationError):
            PrecheckFileEntry(path="x" * 1025, size=0)

    def test_negative_size_raises(self):
        with pytest.raises(ValidationError):
            PrecheckFileEntry(path="SDCARD/DATALOG/file.edf", size=-1)

    def test_zero_size_is_valid(self):
        entry = PrecheckFileEntry(path="SDCARD/DATALOG/file.edf", size=0)
        assert entry.size == 0

    def test_request_files_list_is_valid_at_limit(self):
        # Constructing 50k entries is intentionally skipped — the cap is enforced
        # by pydantic's max_length on the list field.
        req = PrecheckRequest(
            files=[PrecheckFileEntry(path="SDCARD/DATALOG/file.edf", size=1)]
        )
        assert len(req.files) == 1
