"""Tests for proximity-based ResMed session chaining.

Covers ``chain_session_segments`` and ``get_segment_duration_seconds`` from
``snore.parsers.resmed_file_index``.

Synthetic DATALOG dirs are built in ``tmp_path`` with stub EDF headers:
a 256-byte buffer where bytes 236:244 hold ASCII num_records (right-padded
with spaces) and bytes 244:252 hold ASCII record_duration (right-padded),
matching the layout documented in the EDF spec and used by
``get_edf_record_count`` in ``formats/edf.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from snore.parsers.resmed_file_index import (
    OSCAR_COMBINE_CLOSE_SECONDS,
    chain_session_segments,
    get_segment_duration_seconds,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_GAP = OSCAR_COMBINE_CLOSE_SECONDS  # 4 * 3600 = 14400 s


def _make_edf_header(num_records: int, record_duration: float) -> bytes:
    """Build a minimal 256-byte EDF header with the given record params.

    The EDF spec places num_records as 8 ASCII chars at bytes 236:244 and
    record_duration as 8 ASCII chars at bytes 244:252.  We fill the rest
    with spaces (valid EDF padding).
    """
    header = bytearray(b" " * 256)
    # num_records: 8-char ASCII integer, right-padded with spaces
    num_rec_str = str(num_records).ljust(8).encode("ascii")
    header[236:244] = num_rec_str
    # record_duration: 8-char ASCII float, right-padded with spaces
    rec_dur_str = f"{record_duration:g}".ljust(8).encode("ascii")
    header[244:252] = rec_dur_str
    return bytes(header)


def _write_edf(
    path: Path, num_records: int = 1800, record_duration: float = 1.0
) -> Path:
    """Write a stub EDF file with the given header params to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_make_edf_header(num_records, record_duration))
    return path


def _seg(dt: datetime, file_type: str = "BRP") -> str:
    """Return the EDF filename stem for a given datetime and file type."""
    return dt.strftime("%Y%m%d_%H%M%S") + f"_{file_type}.edf"


def _make_segment(
    datalog_dir: Path,
    start: datetime,
    duration_s: float,
    file_types: tuple[str, ...] = ("BRP",),
) -> str:
    """Create stub EDF files for a segment, return the session_id."""
    session_id = start.strftime("%Y%m%d_%H%M%S")
    # EDF record_duration=1s, num_records=duration_s gives correct total.
    for ft in file_types:
        _write_edf(datalog_dir / f"{session_id}_{ft}.edf", int(duration_s), 1.0)
    return session_id


# ---------------------------------------------------------------------------
# get_segment_duration_seconds
# ---------------------------------------------------------------------------


class TestGetSegmentDurationSeconds:
    def test_brp_preferred_over_pld(self, tmp_path):
        seg_id = "20260101_220000"
        brp = _write_edf(
            tmp_path / f"{seg_id}_BRP.edf", num_records=1000, record_duration=2.0
        )
        pld = _write_edf(
            tmp_path / f"{seg_id}_PLD.edf", num_records=500, record_duration=1.0
        )
        files = {"BRP": brp, "PLD": pld}
        assert get_segment_duration_seconds(seg_id, files) == 2000.0

    def test_falls_back_to_pld_when_brp_absent(self, tmp_path):
        seg_id = "20260101_220000"
        pld = _write_edf(
            tmp_path / f"{seg_id}_PLD.edf", num_records=600, record_duration=1.0
        )
        files = {"PLD": pld}
        assert get_segment_duration_seconds(seg_id, files) == 600.0

    def test_falls_back_to_sa2_when_brp_pld_absent(self, tmp_path):
        seg_id = "20260101_220000"
        sa2 = _write_edf(
            tmp_path / f"{seg_id}_SA2.edf", num_records=300, record_duration=2.0
        )
        files = {"SA2": sa2}
        assert get_segment_duration_seconds(seg_id, files) == 600.0

    def test_returns_none_when_no_readable_file(self, tmp_path):
        seg_id = "20260101_220000"
        files: dict[str, Path] = {"BRP": tmp_path / "nonexistent.edf"}
        assert get_segment_duration_seconds(seg_id, files) is None

    def test_negative_one_num_records_returns_none(self, tmp_path):
        """num_records == -1 (EDF+C unknown) → unusable."""
        seg_id = "20260101_220000"
        path = tmp_path / f"{seg_id}_BRP.edf"
        _write_edf(path, num_records=-1, record_duration=1.0)
        assert get_segment_duration_seconds(seg_id, {"BRP": path}) is None

    def test_zero_num_records_returns_none(self, tmp_path):
        seg_id = "20260101_220000"
        path = tmp_path / f"{seg_id}_BRP.edf"
        _write_edf(path, num_records=0, record_duration=1.0)
        assert get_segment_duration_seconds(seg_id, {"BRP": path}) is None

    def test_truncated_header_returns_none(self, tmp_path):
        seg_id = "20260101_220000"
        path = tmp_path / f"{seg_id}_BRP.edf"
        path.write_bytes(b"too short")
        assert get_segment_duration_seconds(seg_id, {"BRP": path}) is None

    def test_empty_files_dict_returns_none(self, tmp_path):
        assert get_segment_duration_seconds("20260101_220000", {}) is None


# ---------------------------------------------------------------------------
# chain_session_segments — empty DATALOG
# ---------------------------------------------------------------------------


class TestChainSessionSegmentsEmpty:
    def test_empty_datalog_returns_empty_list(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        datalog.mkdir()
        assert chain_session_segments(datalog) == []


# ---------------------------------------------------------------------------
# chain_session_segments — basic gap logic
# ---------------------------------------------------------------------------


class TestChainGapLogic:
    def test_two_segments_within_4h_chain_together(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 1, 1, 22, 0, 0)
        _make_segment(datalog, t0, duration_s=3600)  # ends at 23:00
        # Gap = 3h50m (13800s < 14400s threshold) → same chain
        t1 = t0 + timedelta(seconds=3600 + 13800)
        _make_segment(datalog, t1, duration_s=1800)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1
        assert len(chains[0][2]) == 2

    def test_exactly_4h_gap_chains_together(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 1, 1, 22, 0, 0)
        _make_segment(datalog, t0, duration_s=3600)
        # Gap == exactly 4h (== threshold) → chains
        t1 = t0 + timedelta(seconds=3600 + _MAX_GAP)
        _make_segment(datalog, t1, duration_s=1800)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1

    def test_gap_just_over_4h_splits_into_two_chains(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 1, 1, 22, 0, 0)
        _make_segment(datalog, t0, duration_s=3600)
        # Gap = 4h + 1s > threshold → split
        t1 = t0 + timedelta(seconds=3600 + _MAX_GAP + 1)
        _make_segment(datalog, t1, duration_s=1800)

        chains = chain_session_segments(datalog)
        assert len(chains) == 2
        assert len(chains[0][2]) == 1
        assert len(chains[1][2]) == 1

    def test_chains_are_chronologically_ordered(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 1, 1, 22, 0, 0)
        t1 = t0 + timedelta(hours=10)
        t2 = t1 + timedelta(hours=10)
        _make_segment(datalog, t0, duration_s=3600)
        _make_segment(datalog, t1, duration_s=3600)
        _make_segment(datalog, t2, duration_s=3600)

        chains = chain_session_segments(datalog)
        ids = [c[1] for c in chains]
        assert ids == sorted(ids)

    def test_chain_id_equals_first_segment_session_id(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 1, 10, 22, 0, 0)
        t1 = t0 + timedelta(minutes=30)
        first_id = _make_segment(datalog, t0, duration_s=1800)
        _make_segment(datalog, t1, duration_s=1800)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1
        assert chains[0][1] == first_id


# ---------------------------------------------------------------------------
# chain_session_segments — noon-rollover scenario
# ---------------------------------------------------------------------------


class TestNoonRollover:
    def test_noon_rollover_pair_chains_into_one_session(self, tmp_path):
        """Two segments separated by ~40s spanning noon → one chain, night = previous day."""
        datalog = tmp_path / "DATALOG"
        # Segment ends just before noon, next starts just after noon (~40s gap).
        pre_noon_start = datetime(2026, 1, 15, 11, 30, 0)
        pre_noon_duration = 29 * 60 + 57  # ends at 11:59:57 approx
        _make_segment(datalog, pre_noon_start, duration_s=pre_noon_duration)

        post_noon_start = datetime(2026, 1, 15, 12, 0, 3)  # gap ~6s from end
        _make_segment(datalog, post_noon_start, duration_s=3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1
        # night_date uses noon-cutoff on chain_start (pre-noon → previous calendar day)
        assert chains[0][0] == "20260114"

    def test_noon_rollover_chain_contains_both_segments(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        pre_start = datetime(2026, 3, 20, 11, 45, 0)
        _make_segment(datalog, pre_start, duration_s=900)  # 15 min, ends at ~11:59:xx
        post_start = datetime(2026, 3, 20, 12, 0, 10)
        post_id = _make_segment(datalog, post_start, duration_s=3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1
        assert post_id in chains[0][2]


# ---------------------------------------------------------------------------
# chain_session_segments — diagnostic blip isolation
# ---------------------------------------------------------------------------


class TestDiagnosticBlip:
    def test_isolated_blip_5h_before_sleep_forms_separate_chain(self, tmp_path):
        """A 2-min blip at 18:46 followed by sleep at 00:30 → two chains."""
        datalog = tmp_path / "DATALOG"
        blip_start = datetime(2026, 2, 5, 18, 46, 0)
        blip_id = _make_segment(datalog, blip_start, duration_s=120)  # 2-min blip

        # 5.76 h after blip end = 5 h 44 min 48 s > 4 h threshold
        sleep_start = datetime(2026, 2, 6, 0, 30, 0)
        sleep_id = _make_segment(datalog, sleep_start, duration_s=7 * 3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 2

        chain_ids = [c[1] for c in chains]
        assert blip_id in chain_ids
        assert sleep_id in chain_ids

    def test_two_chains_have_distinct_chain_ids(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t_blip = datetime(2026, 2, 5, 18, 0, 0)
        t_sleep = datetime(2026, 2, 5, 23, 30, 0)
        blip_id = _make_segment(datalog, t_blip, duration_s=60)
        sleep_id = _make_segment(datalog, t_sleep, duration_s=7 * 3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 2
        assert chains[0][1] != chains[1][1]
        assert chains[0][1] == blip_id
        assert chains[1][1] == sleep_id


# ---------------------------------------------------------------------------
# chain_session_segments — unknown-duration lower-bound behaviour
# ---------------------------------------------------------------------------


class TestUnknownDurationLowerBound:
    def test_unreadable_segment_within_threshold_chains_together(self, tmp_path):
        """Unknown duration → start time used as lower-bound end; near neighbour chains."""
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 4, 1, 22, 0, 0)
        # All-space header → int("") raises → None duration → lower bound = t0 itself
        bad_path = datalog / f"{t0.strftime('%Y%m%d_%H%M%S')}_BRP.edf"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_bytes(b" " * 256)

        # 1 min later: gap measured from t0 (lower bound) = 60 s ≤ 4 h → chains
        t1 = t0 + timedelta(minutes=1)
        _make_segment(datalog, t1, duration_s=3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1, (
            "Unknown duration uses start time as lower bound, so nearby segment still chains"
        )

    def test_unreadable_segment_followed_by_far_segment_splits(self, tmp_path):
        """Unknown duration + next segment > 4 h away → still splits (lower bound too old)."""
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 4, 1, 22, 0, 0)
        bad_path = datalog / f"{t0.strftime('%Y%m%d_%H%M%S')}_BRP.edf"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_bytes(b" " * 256)

        # > 4 h after t0 (the lower bound) → splits
        t1 = t0 + timedelta(hours=5)
        _make_segment(datalog, t1, duration_s=3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 2

    def test_zero_num_records_within_threshold_chains_together(self, tmp_path):
        """Zero num_records → None duration → lower-bound end = seg_start → chains nearby."""
        datalog = tmp_path / "DATALOG"
        t0 = datetime(2026, 4, 2, 22, 0, 0)
        _write_edf(datalog / f"{t0.strftime('%Y%m%d_%H%M%S')}_BRP.edf", num_records=0)

        t1 = t0 + timedelta(minutes=5)
        _make_segment(datalog, t1, duration_s=3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1


# ---------------------------------------------------------------------------
# chain_session_segments — real CSL/EVE stub pairing (verified on prod data)
# ---------------------------------------------------------------------------


class TestCslEveStubPairing:
    def test_csl_eve_stub_before_brp_group_chains_together(self, tmp_path):
        """CSL/EVE-only stub (no duration) 9 s before BRP group chains into one segment."""
        datalog = tmp_path / "DATALOG"
        stub_start = datetime(2026, 1, 30, 3, 19, 24)
        stub_id = _make_segment(
            datalog, stub_start, duration_s=1, file_types=("CSL", "EVE")
        )
        # CSL/EVE stubs have no BRP/PLD/SA2 → get_segment_duration_seconds returns None

        brp_start = datetime(2026, 1, 30, 3, 19, 33)  # 9 s later
        brp_id = _make_segment(datalog, brp_start, duration_s=31200)  # 8 h 40 min

        chains = chain_session_segments(datalog)
        assert len(chains) == 1
        assert stub_id in chains[0][2]
        assert brp_id in chains[0][2]

    def test_noon_rollover_with_csl_eve_stubs_chains_into_one_session(self, tmp_path):
        """Real DATALOG/20260130 structure: 4 groups → 1 chain, night = 20260129.

        Groups (verified on prod):
          031924 CSL+EVE   (stub, no duration)
          031933 BRP+PLD+SA2  (31200 s → ends at ~11:59:33)
          120000 CSL+EVE   (stub, no duration, gap from 11:59:33 = ~27 s)
          120009 BRP+PLD+SA2  (8580 s)
        """
        datalog = tmp_path / "DATALOG"
        # Pre-noon stub + waveform pair
        _make_segment(
            datalog,
            datetime(2026, 1, 30, 3, 19, 24),
            duration_s=0,
            file_types=("CSL", "EVE"),
        )
        _make_segment(
            datalog, datetime(2026, 1, 30, 3, 19, 33), duration_s=31200
        )  # ends ~11:59:33

        # Post-noon stub + waveform pair
        _make_segment(
            datalog,
            datetime(2026, 1, 30, 12, 0, 0),
            duration_s=0,
            file_types=("CSL", "EVE"),
        )
        _make_segment(datalog, datetime(2026, 1, 30, 12, 0, 9), duration_s=8580)

        chains = chain_session_segments(datalog)
        assert len(chains) == 1, (
            f"Expected 1 chain, got {len(chains)}: {[c[1] for c in chains]}"
        )
        assert chains[0][1] == "20260130_031924", (
            "chain_id must be first (stub) segment"
        )
        assert chains[0][0] == "20260129", (
            "night_date must be previous day (pre-noon chain start)"
        )
        assert len(chains[0][2]) == 4, "all 4 segment groups must be in one chain"

    def test_noon_rollover_then_distant_sleep_splits_correctly(self, tmp_path):
        """A diagnostic blip (stub+waveform) + real sleep > 4 h later → two chains."""
        datalog = tmp_path / "DATALOG"
        # Blip at 18:46 (stub + 2-min waveform)
        _make_segment(
            datalog,
            datetime(2026, 2, 5, 18, 46, 0),
            duration_s=0,
            file_types=("CSL", "EVE"),
        )
        blip_id = _make_segment(
            datalog, datetime(2026, 2, 5, 18, 46, 9), duration_s=120
        )

        # Sleep at 00:33 → gap from blip end (18:48:09) = 5 h 44 min 51 s > 4 h
        _make_segment(
            datalog,
            datetime(2026, 2, 6, 0, 33, 0),
            duration_s=0,
            file_types=("CSL", "EVE"),
        )
        sleep_id = _make_segment(
            datalog, datetime(2026, 2, 6, 0, 33, 9), duration_s=7 * 3600
        )

        chains = chain_session_segments(datalog)
        assert len(chains) == 2
        blip_chain_ids = set(chains[0][2].keys())
        sleep_chain_ids = set(chains[1][2].keys())
        assert blip_id in blip_chain_ids
        assert sleep_id in sleep_chain_ids
        assert len(chains) == 2


# ---------------------------------------------------------------------------
# chain_session_segments — night_date assignment
# ---------------------------------------------------------------------------


class TestNightDateAssignment:
    def test_pre_noon_start_night_date_is_previous_day(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t = datetime(2026, 5, 10, 2, 30, 0)  # 02:30 → night of May 9
        _make_segment(datalog, t, duration_s=3600)
        chains = chain_session_segments(datalog)
        assert chains[0][0] == "20260509"

    def test_post_noon_start_night_date_is_same_day(self, tmp_path):
        datalog = tmp_path / "DATALOG"
        t = datetime(2026, 5, 10, 22, 0, 0)  # 22:00 → night of May 10
        _make_segment(datalog, t, duration_s=3600)
        chains = chain_session_segments(datalog)
        assert chains[0][0] == "20260510"

    def test_multiple_chains_same_night_date_have_distinct_chain_ids(self, tmp_path):
        """A blip and main sleep on the same night → same night_date, distinct chain_ids."""
        datalog = tmp_path / "DATALOG"
        t_blip = datetime(2026, 2, 5, 18, 0, 0)  # 18:00 → night of Feb 5
        t_sleep = datetime(2026, 2, 5, 23, 30, 0)  # 23:30 → night of Feb 5
        blip_id = _make_segment(datalog, t_blip, duration_s=60)
        sleep_id = _make_segment(datalog, t_sleep, duration_s=7 * 3600)

        chains = chain_session_segments(datalog)
        assert len(chains) == 2
        # Both map to the same night date (post-noon, same day)
        assert chains[0][0] == chains[1][0] == "20260205"
        chain_ids = {c[1] for c in chains}
        assert blip_id in chain_ids
        assert sleep_id in chain_ids
