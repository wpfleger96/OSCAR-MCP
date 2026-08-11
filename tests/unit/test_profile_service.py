"""Unit tests for profile_service helpers.

Covers quarantine_profile_raw_dir behavior and purge_profile_raw_dir delegation.
"""

from __future__ import annotations

from snore.services.profile_service import (
    purge_profile_raw_dir,
    quarantine_profile_raw_dir,
)


class TestQuarantineProfileRawDir:
    """Tests for the atomic rename step of the two-phase purge."""

    def test_absent_source_returns_none(self, tmp_path):
        """When the source dir does not exist, returns None and creates no quarantine entry."""
        raw_root = tmp_path / "raw"
        raw_root.mkdir()

        result = quarantine_profile_raw_dir(99, raw_root)

        assert result is None
        # No .quarantine/99 should have been created.
        quarantine_dir = raw_root / ".quarantine" / "99"
        assert not quarantine_dir.exists()

    def test_existing_source_renamed_and_dst_returned(self, tmp_path):
        """Source dir is atomically renamed to quarantine; dst path is returned."""
        raw_root = tmp_path / "raw"
        profile_dir = raw_root / "42"
        profile_dir.mkdir(parents=True)
        (profile_dir / "session.edf").write_text("data")

        dst = quarantine_profile_raw_dir(42, raw_root)

        assert dst is not None
        assert dst == raw_root / ".quarantine" / "42"
        assert dst.exists()
        assert (dst / "session.edf").read_text() == "data"
        assert not profile_dir.exists(), "Source dir must be gone after rename"

    def test_stale_dst_replaced_before_rename(self, tmp_path):
        """An existing stale quarantine destination is replaced cleanly."""
        raw_root = tmp_path / "raw"
        quarantine = raw_root / ".quarantine"

        # Create stale quarantine entry (old data).
        stale_dst = quarantine / "7"
        stale_dst.mkdir(parents=True)
        (stale_dst / "old.edf").write_text("stale")

        # Create fresh source dir (new data).
        src = raw_root / "7"
        src.mkdir(parents=True)
        (src / "new.edf").write_text("fresh")

        dst = quarantine_profile_raw_dir(7, raw_root)

        assert dst is not None
        assert (dst / "new.edf").read_text() == "fresh"
        assert not (dst / "old.edf").exists(), "Stale content must be gone"
        assert not src.exists(), "Source must be gone after rename"

    def test_quarantine_root_created_if_absent(self, tmp_path):
        """quarantine mkdir is called even when .quarantine/ does not exist yet."""
        raw_root = tmp_path / "raw"
        src = raw_root / "5"
        src.mkdir(parents=True)
        (src / "data.edf").write_text("x")

        dst = quarantine_profile_raw_dir(5, raw_root)

        assert dst is not None
        assert (raw_root / ".quarantine").is_dir()
        assert dst.exists()

    def test_idempotent_for_absent_source_after_rename(self, tmp_path):
        """Calling again after source is already moved returns None (idempotent)."""
        raw_root = tmp_path / "raw"
        src = raw_root / "3"
        src.mkdir(parents=True)

        # First call moves it.
        quarantine_profile_raw_dir(3, raw_root)
        assert not src.exists()

        # Second call on the now-absent source returns None.
        result = quarantine_profile_raw_dir(3, raw_root)
        assert result is None


class TestPurgeProfileRawDirDelegation:
    """purge_profile_raw_dir must delegate to quarantine_profile_raw_dir + rmtree."""

    def test_absent_dir_is_no_op(self, tmp_path):
        """No exception when the source dir does not exist."""
        raw_root = tmp_path / "raw"
        raw_root.mkdir()
        purge_profile_raw_dir(99, raw_root)  # must not raise

    def test_existing_dir_fully_removed(self, tmp_path):
        """After purge, neither the source nor the quarantine dst exists."""
        raw_root = tmp_path / "raw"
        profile_dir = raw_root / "10"
        profile_dir.mkdir(parents=True)
        (profile_dir / "data.edf").write_text("content")

        purge_profile_raw_dir(10, raw_root)

        assert not profile_dir.exists()
        quarantine_dst = raw_root / ".quarantine" / "10"
        assert not quarantine_dst.exists()
