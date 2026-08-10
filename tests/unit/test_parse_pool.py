"""Unit tests for the parse-dedicated process pool singleton."""

from __future__ import annotations

import time

from concurrent.futures import ProcessPoolExecutor

import pytest

import snore.utils.parse_pool as _pp_module
import snore.utils.process_pool as _compute_module


def _shutdown_and_reset() -> None:
    """Reset module state between tests."""
    _pp_module.shutdown_pool(wait=True)
    # Ensure the singleton is cleared even if shutdown_pool had an error.
    with _pp_module._pool_lock:
        _pp_module._pool = None


@pytest.fixture(autouse=True)
def reset_pool():
    """Reset the parse pool before and after every test in this module."""
    _shutdown_and_reset()
    yield
    _shutdown_and_reset()


# ---------------------------------------------------------------------------
# Lazy initialisation
# ---------------------------------------------------------------------------


def test_pool_none_before_first_get():
    assert _pp_module._pool is None


def test_get_pool_returns_executor():
    pool = _pp_module.get_pool()
    assert isinstance(pool, ProcessPoolExecutor)


# ---------------------------------------------------------------------------
# Singleton identity
# ---------------------------------------------------------------------------


def test_get_pool_singleton():
    pool_a = _pp_module.get_pool()
    pool_b = _pp_module.get_pool()
    assert pool_a is pool_b


# ---------------------------------------------------------------------------
# shutdown_pool clears singleton; next get_pool recreates
# ---------------------------------------------------------------------------


def test_shutdown_pool_clears_singleton():
    _pp_module.get_pool()
    assert _pp_module._pool is not None
    _pp_module.shutdown_pool(wait=True)
    assert _pp_module._pool is None


def test_get_pool_after_shutdown_creates_new():
    first = _pp_module.get_pool()
    _pp_module.shutdown_pool(wait=True)
    second = _pp_module.get_pool()
    assert isinstance(second, ProcessPoolExecutor)
    assert second is not first


# ---------------------------------------------------------------------------
# Broken pool is replaced
# ---------------------------------------------------------------------------


def test_broken_pool_replaced():
    pool_a = _pp_module.get_pool()
    pool_a._broken = True
    pool_b = _pp_module.get_pool()
    assert pool_b is not pool_a
    assert isinstance(pool_b, ProcessPoolExecutor)
    assert not getattr(pool_b, "_broken", False)


# ---------------------------------------------------------------------------
# Parse pool is separate from compute pool
# ---------------------------------------------------------------------------


def test_parse_pool_distinct_from_compute_pool():
    """parse_pool.get_pool() must return a different object than process_pool.get_pool()."""
    parse_pool = _pp_module.get_pool()
    compute_pool = _compute_module.get_pool()
    assert parse_pool is not compute_pool


# ---------------------------------------------------------------------------
# Default sizing uses cpu_count // 2, minimum 1
# ---------------------------------------------------------------------------


def test_default_max_workers_at_least_one(monkeypatch):
    """Default parse pool size is max(1, cpu_count // 2), never zero."""
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    size = _pp_module._read_max_workers()
    assert size >= 1


def test_default_max_workers_half_cpu(monkeypatch):
    """Default parse pool size is cpu_count // 2 when cpu_count >= 2."""
    monkeypatch.setattr("os.cpu_count", lambda: 8)

    # Force the config-read fallback so the real max(1, cpu//2) formula executes.
    def _no_config() -> None:
        raise RuntimeError("no config in test environment")

    monkeypatch.setattr("snore.api.config.get_config", _no_config)
    size = _pp_module._read_max_workers()
    assert size == 4


# ---------------------------------------------------------------------------
# cancel_pending cancels unstarted futures
# ---------------------------------------------------------------------------


def _sleep_forever():  # noqa: D401
    time.sleep(60)


def _return_42() -> int:
    return 42


def test_cancel_pending_cancels_unstarted():
    pool = ProcessPoolExecutor(max_workers=1)
    try:
        # Saturate the single worker so subsequent submits queue as pending.
        _ = pool.submit(_sleep_forever)
        pending = [pool.submit(_sleep_forever) for _ in range(3)]
        _pp_module.cancel_pending(pending)
        # Give the pool a moment to propagate the cancels.
        cancelled = [f.cancelled() for f in pending]
        assert any(cancelled), "At least one future should be cancelled"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_cancel_pending_skips_done_futures():
    pool = ProcessPoolExecutor(max_workers=1)
    try:
        f = pool.submit(_return_42)
        f.result(timeout=5)
        assert f.done()
        # Should not raise even though the future is already done.
        _pp_module.cancel_pending([f])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# CPython _broken sentinel existence guard
# ---------------------------------------------------------------------------


def test_broken_sentinel_exists():
    """ProcessPoolExecutor must expose a ``_broken`` attribute.

    If CPython renames this private attribute, this test fails in CI before
    the silent regression reaches production — ``_is_broken()`` would always
    return False, and a crashed pool would be returned forever.
    """
    pool = ProcessPoolExecutor(max_workers=1)
    try:
        assert hasattr(pool, "_broken"), (
            "ProcessPoolExecutor._broken not found — CPython may have renamed it; "
            "update _is_broken() in src/snore/utils/parse_pool.py"
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
