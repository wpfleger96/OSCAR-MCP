"""Tests for process-pool integration in parsers and analysis compute.

Covers:
- Picklability of module-level worker functions
- RawSessionBlobs pickle round-trip
- BrokenProcessPool → RuntimeError propagation
- GeneratorExit on generator .close() calls cancel_pending
"""

from __future__ import annotations

import pickle

from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import snore.utils.process_pool as _pp_module


def _shutdown_pool() -> None:
    _pp_module.shutdown_pool(wait=True)
    with _pp_module._pool_lock:
        _pp_module._pool = None


@pytest.fixture(autouse=True)
def reset_pool():
    """Ensure the shared pool is reset after each test in this module."""
    _shutdown_pool()
    yield
    _shutdown_pool()


# ---------------------------------------------------------------------------
# Picklability of module-level worker functions
# ---------------------------------------------------------------------------


def test_resmed_worker_is_picklable():
    from snore.parsers.resmed_edf import _resmed_parse_bundle_worker

    # Must not raise — spawn context requires all submitted callables to pickle.
    data = pickle.dumps(_resmed_parse_bundle_worker)
    assert len(data) > 0


def test_oscar_worker_is_picklable():
    from snore.parsers.oscar_device import _oscar_parse_session_worker

    data = pickle.dumps(_oscar_parse_session_worker)
    assert len(data) > 0


def test_compute_session_in_process_is_picklable():
    from snore.analysis.service import _compute_session_in_process

    data = pickle.dumps(_compute_session_in_process)
    assert len(data) > 0


# ---------------------------------------------------------------------------
# RawSessionBlobs pickle round-trip
# ---------------------------------------------------------------------------


def test_raw_session_blobs_pickle_round_trip():
    from snore.analysis.service import RawSessionBlobs

    raw = RawSessionBlobs(
        session_id=1,
        flow_blob=b"\x00\x01\x02",
        flow_sample_count=3,
        flow_metadata={"sample_rate": 25.0},
        machine_events=[],
    )
    restored = pickle.loads(pickle.dumps(raw))
    assert restored.session_id == 1
    assert restored.flow_blob == b"\x00\x01\x02"
    assert restored.flow_metadata == {"sample_rate": 25.0}
    assert restored.machine_events == []


# ---------------------------------------------------------------------------
# BrokenProcessPool → RuntimeError propagation (resmed parser)
# ---------------------------------------------------------------------------


def test_resmed_broken_pool_raises_runtime_error():
    from snore.parsers.resmed_edf import ResmedEDFParser

    parser = ResmedEDFParser()
    nights = [("20240101", "20240101_000000", {}), ("20240102", "20240102_000000", {})]

    broken_pool = MagicMock()
    broken_pool.submit.side_effect = BrokenProcessPool("simulated crash")

    with (
        patch.object(
            parser, "_discover_session_files", return_value=(Path("/data"), nights)
        ),
        patch.object(
            parser, "_filter_night_items", side_effect=lambda items, *a: items
        ),
        patch.object(parser, "_load_str_caches", return_value=(None, None)),
        patch.object(parser, "get_device_info", return_value=MagicMock()),
        patch("snore.parsers.resmed_edf.get_pool", return_value=broken_pool),
    ):
        with pytest.raises(RuntimeError, match="crashed"):
            list(parser.parse_sessions(Path("/data"), parallel=True))


# ---------------------------------------------------------------------------
# BrokenProcessPool → RuntimeError propagation (oscar parser)
# ---------------------------------------------------------------------------


def test_oscar_broken_pool_raises_runtime_error():
    from snore.parsers.oscar_device import OscarDeviceParser

    parser = OscarDeviceParser()
    device_info = MagicMock()
    session_files = [(1, Path("/data/1.000"), Path("/data/1.001"))]

    broken_pool = MagicMock()
    broken_pool.submit.side_effect = BrokenProcessPool("simulated crash")

    with patch("snore.parsers.oscar_device.get_pool", return_value=broken_pool):
        with pytest.raises(RuntimeError, match="crashed"):
            list(
                parser._parse_sessions_parallel(
                    session_files,
                    device_info,
                    Path("/data"),
                    None,
                    None,
                    None,
                )
            )


# ---------------------------------------------------------------------------
# GeneratorExit on .close() must call cancel_pending so pool slots are freed
# ---------------------------------------------------------------------------


def _make_mock_session(night_date: str) -> MagicMock:
    s = MagicMock()
    s.start_time = datetime.fromisoformat(
        f"{night_date[:4]}-{night_date[4:6]}-{night_date[6:8]}T22:00:00"
    )
    return s


def test_resmed_generator_close_cancels_pending():
    """Calling .close() on a live resmed generator triggers cancel_pending."""
    from snore.parsers.resmed_edf import ResmedEDFParser

    parser = ResmedEDFParser()
    nights = [
        (f"2024010{i}", f"2024010{i}_000000", {}) for i in range(1, 4)
    ]  # 3 nights

    # f0 is pre-resolved so as_completed yields it immediately.
    # f1 and f2 stay pending so we can assert they were cancelled.
    f0, f1, f2 = Future(), Future(), Future()
    f0.set_result(_make_mock_session("20240101"))

    mock_pool = MagicMock()
    mock_pool.submit.side_effect = [f0, f1, f2]

    with (
        patch.object(
            parser, "_discover_session_files", return_value=(Path("/data"), nights)
        ),
        patch.object(
            parser, "_filter_night_items", side_effect=lambda items, *a: items
        ),
        patch.object(parser, "_load_str_caches", return_value=(None, None)),
        patch.object(parser, "get_device_info", return_value=MagicMock()),
        patch("snore.parsers.resmed_edf.get_pool", return_value=mock_pool),
    ):
        gen = parser.parse_sessions(Path("/data"), parallel=True)
        next(gen)  # consume night1; generator suspends at yield
        gen.close()  # GeneratorExit → finally: cancel_pending(futures)

    assert f1.cancelled(), "f1 must be cancelled after generator .close()"
    assert f2.cancelled(), "f2 must be cancelled after generator .close()"


def test_oscar_generator_close_cancels_pending():
    """Calling .close() on a live oscar generator triggers cancel_pending."""
    from snore.parsers.oscar_device import OscarDeviceParser

    parser = OscarDeviceParser()
    session_files = [
        (i, Path(f"/data/{i}.000"), Path(f"/data/{i}.001")) for i in range(1, 4)
    ]
    device_info = MagicMock()

    f0, f1, f2 = Future(), Future(), Future()
    f0.set_result(_make_mock_session("20240101"))

    mock_pool = MagicMock()
    mock_pool.submit.side_effect = [f0, f1, f2]

    with patch("snore.parsers.oscar_device.get_pool", return_value=mock_pool):
        gen = parser._parse_sessions_parallel(
            session_files, device_info, Path("/data"), None, None, None
        )
        next(gen)  # consume session1; generator suspends at yield
        gen.close()  # GeneratorExit → finally: cancel_pending(futures)

    assert f1.cancelled(), "f1 must be cancelled after generator .close()"
    assert f2.cancelled(), "f2 must be cancelled after generator .close()"


# ---------------------------------------------------------------------------
# BrokenProcessPool raised by future.result() (mid-run worker crash)
# — must not be swallowed by the inner except Exception handler
# ---------------------------------------------------------------------------


def test_resmed_broken_pool_from_future_result_raises_runtime_error():
    """BrokenProcessPool from future.result() propagates as RuntimeError."""
    from concurrent.futures import Future

    from snore.parsers.resmed_edf import ResmedEDFParser

    parser = ResmedEDFParser()
    nights = [("20240101", "20240101_000000", {}), ("20240102", "20240102_000000", {})]

    crashed_future: Future = Future()
    crashed_future.set_exception(BrokenProcessPool("worker died mid-run"))

    mock_pool = MagicMock()
    mock_pool.submit.return_value = crashed_future

    with (
        patch.object(
            parser, "_discover_session_files", return_value=(Path("/data"), nights)
        ),
        patch.object(
            parser, "_filter_night_items", side_effect=lambda items, *a: items
        ),
        patch.object(parser, "_load_str_caches", return_value=(None, None)),
        patch.object(parser, "get_device_info", return_value=MagicMock()),
        patch("snore.parsers.resmed_edf.get_pool", return_value=mock_pool),
    ):
        with pytest.raises(RuntimeError, match="crashed"):
            list(parser.parse_sessions(Path("/data"), parallel=True))


def test_oscar_broken_pool_from_future_result_raises_runtime_error():
    """BrokenProcessPool from future.result() propagates as RuntimeError."""
    from concurrent.futures import Future

    from snore.parsers.oscar_device import OscarDeviceParser

    parser = OscarDeviceParser()
    device_info = MagicMock()
    session_files = [(1, Path("/data/1.000"), Path("/data/1.001"))]

    crashed_future: Future = Future()
    crashed_future.set_exception(BrokenProcessPool("worker died mid-run"))

    mock_pool = MagicMock()
    mock_pool.submit.return_value = crashed_future

    with patch("snore.parsers.oscar_device.get_pool", return_value=mock_pool):
        with pytest.raises(RuntimeError, match="crashed"):
            list(
                parser._parse_sessions_parallel(
                    session_files,
                    device_info,
                    Path("/data"),
                    None,
                    None,
                    None,
                )
            )


# ---------------------------------------------------------------------------
# STR cache slicing: parallel path submits per-night slices, not full caches
# ---------------------------------------------------------------------------


def test_parallel_submits_per_night_str_cache_slices():
    """Each pool.submit call receives a per-chain cache slice, not the full cache.

    Before the fix, every future received the full multi-night dict —
    O(nights²) pickle cost.  After the fix each future receives only the
    entries whose therapy days overlap with the chain's segments.
    """
    from snore.parsers.resmed_edf import ResmedEDFParser

    parser = ResmedEDFParser()
    d1 = date(2025, 1, 1)
    d2 = date(2025, 1, 2)
    # Segment ids chosen so therapy_day (seg_start - 12h) maps to d1/d2:
    # 20250101_220000 - 12h → 2025-01-01 10:00 → d1
    # 20250102_220000 - 12h → 2025-01-02 10:00 → d2
    nights = [
        (
            "20250101",
            "20250101_220000",
            {"20250101_220000": {"BRP": Path("/fake/1.edf")}},
        ),
        (
            "20250102",
            "20250102_220000",
            {"20250102_220000": {"BRP": Path("/fake/2.edf")}},
        ),
    ]

    full_settings = {d1: {"pressure_min": 4.0}, d2: {"pressure_min": 6.0}}
    full_summaries = {d1: {"ahi": 1.0}, d2: {"ahi": 2.0}}

    # Pre-resolved futures that return None (nights skipped, no sessions yielded).
    f1, f2 = Future(), Future()
    f1.set_result(None)
    f2.set_result(None)

    mock_pool = MagicMock()
    mock_pool.submit.side_effect = [f1, f2]

    with (
        patch.object(
            parser, "_discover_session_files", return_value=(Path("/data"), nights)
        ),
        patch.object(
            parser, "_filter_night_items", side_effect=lambda items, *a: items
        ),
        patch.object(
            parser, "_load_str_caches", return_value=(full_settings, full_summaries)
        ),
        patch.object(parser, "get_device_info", return_value=MagicMock()),
        patch("snore.parsers.resmed_edf.get_pool", return_value=mock_pool),
    ):
        list(parser.parse_sessions(Path("/data"), parallel=True))

    calls = mock_pool.submit.call_args_list
    assert len(calls) == 2

    # pool.submit(callable, night_date, chain_id, segments, device_info, path,
    #             str_settings_cache, str_summaries_cache, ...)
    # args indices:  [0]       [1]       [2]       [3]      [4]    [5]
    #                [6]=settings  [7]=summaries
    expected_chain_ids = {n[0]: n[1] for n in nights}
    for call_obj in calls:
        a = call_obj.args
        night_key = datetime.strptime(a[1], "%Y%m%d").date()
        chain_id_arg = a[2]
        settings_arg = a[6]
        summaries_arg = a[7]

        assert chain_id_arg == expected_chain_ids[a[1]], (
            f"chain_id for night {a[1]} must be {expected_chain_ids[a[1]]}; got {chain_id_arg}"
        )
        assert settings_arg == {night_key: full_settings[night_key]}, (
            f"Expected single-entry settings slice for {night_key}; got {settings_arg}"
        )
        assert summaries_arg == {night_key: full_summaries[night_key]}, (
            f"Expected single-entry summaries slice for {night_key}; got {summaries_arg}"
        )


def test_absent_str_entry_submits_none_cache():
    """A chain with no STR entry gets None (not an empty dict) as its cache slice."""
    from snore.parsers.resmed_edf import ResmedEDFParser

    parser = ResmedEDFParser()
    d_known = date(2025, 1, 2)
    # Segment ids: therapy_day for 20250101_220000 → d1 (absent); for 20250102_220000 → d_known.
    nights = [
        (
            "20250101",
            "20250101_220000",
            {"20250101_220000": {"BRP": Path("/fake/1.edf")}},
        ),
        (
            "20250102",
            "20250102_220000",
            {"20250102_220000": {"BRP": Path("/fake/2.edf")}},
        ),
    ]

    # Only d_known has an STR entry; therapy day for 20250101 is absent.
    full_settings = {d_known: {"pressure_min": 6.0}}

    f1, f2 = Future(), Future()
    f1.set_result(None)
    f2.set_result(None)

    mock_pool = MagicMock()
    mock_pool.submit.side_effect = [f1, f2]

    with (
        patch.object(
            parser, "_discover_session_files", return_value=(Path("/data"), nights)
        ),
        patch.object(
            parser, "_filter_night_items", side_effect=lambda items, *a: items
        ),
        patch.object(parser, "_load_str_caches", return_value=(full_settings, None)),
        patch.object(parser, "get_device_info", return_value=MagicMock()),
        patch("snore.parsers.resmed_edf.get_pool", return_value=mock_pool),
    ):
        list(parser.parse_sessions(Path("/data"), parallel=True))

    calls = mock_pool.submit.call_args_list
    assert len(calls) == 2

    # Key by night_date (a[1]); settings at a[6], summaries at a[7].
    by_night = {call_obj.args[1]: call_obj.args for call_obj in calls}

    # Chain with no STR entry → None passed for both caches.
    args_missing = by_night["20250101"]
    assert args_missing[6] is None, (
        "Missing STR entry should pass None, not an empty dict"
    )
    assert args_missing[7] is None

    # Chain with an entry → single-entry slice.
    args_present = by_night["20250102"]
    assert args_present[6] == {d_known: {"pressure_min": 6.0}}
