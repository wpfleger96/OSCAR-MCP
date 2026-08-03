"""Adversarial tests for the PENDING_UPLOAD admission state machine.

Covers:
- Slot-reuse at cap: for each release path (try_cancel on PENDING_UPLOAD/PENDING,
  release_capacity after terminal/failed/cancelled), the cap becomes available again.
- Cleanup-ordering: capacity is NOT released until after cleanup_files() completes.
- Atomic reservation→job conversion: the counter never double-counts or drops.
- Per-user and global cap enforcement.
- No-duplicate-on-retry: run_txn idempotent units do not duplicate rows under contention.
"""

from __future__ import annotations

import threading
import time

import pytest

import snore.api.import_jobs as ij

from snore.api.import_jobs import (
    MAX_ACTIVE_GLOBAL,
    MAX_ACTIVE_PER_USER,
    ImportJob,
    JobState,
    reserve_slot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_all_jobs() -> None:
    """Remove ALL jobs from the store and reset counters (test isolation)."""
    with ij._lock:
        ij._jobs.clear()
    with ij._counts_lock:
        ij._per_user_count.clear()
        ij._global_count = 0


def _active_count_for(owner: int | None) -> int:
    with ij._counts_lock:
        return ij._per_user_count.get(owner, 0)


def _global_count() -> int:
    with ij._counts_lock:
        return ij._global_count


@pytest.fixture(autouse=True)
def isolate_job_store():
    """Reset the in-memory job store before and after each test."""
    _drain_all_jobs()
    yield
    _drain_all_jobs()


# ---------------------------------------------------------------------------
# Per-user cap enforcement
# ---------------------------------------------------------------------------


class TestPerUserCapEnforcement:
    def test_per_user_cap_rejects_over_limit_request(self):
        """MAX_ACTIVE_PER_USER+1 reservations from the same user → last is rejected."""
        owner = 42
        jobs = []
        for _ in range(MAX_ACTIVE_PER_USER):
            j = reserve_slot(owner)
            assert j is not None, "Reservation within cap should succeed"
            jobs.append(j)

        over_limit = reserve_slot(owner)
        assert over_limit is None, "Reservation over per-user cap must return None"

    def test_different_users_have_independent_caps(self):
        """Two users each fill their per-user cap without affecting each other."""
        user_a, user_b = 1, 2
        for _ in range(MAX_ACTIVE_PER_USER):
            assert reserve_slot(user_a) is not None
        for _ in range(MAX_ACTIVE_PER_USER):
            assert reserve_slot(user_b) is not None


class TestGlobalCapEnforcement:
    def test_global_cap_rejects_when_all_slots_full(self):
        """Fill the global cap across multiple users; next reservation is rejected."""
        # Use distinct users so per-user caps don't hit first.
        slots_reserved = 0
        for user_id in range(MAX_ACTIVE_GLOBAL + 2):
            if slots_reserved >= MAX_ACTIVE_GLOBAL:
                break
            for _ in range(MAX_ACTIVE_PER_USER):
                if slots_reserved >= MAX_ACTIVE_GLOBAL:
                    break
                j = reserve_slot(user_id * 100)
                if j is not None:
                    slots_reserved += 1

        assert _global_count() == MAX_ACTIVE_GLOBAL
        # Any further reservation must fail.
        assert reserve_slot(9999) is None, "Global cap must block new reservations"


# ---------------------------------------------------------------------------
# Slot-reuse at cap — one test per release path
# ---------------------------------------------------------------------------


class TestSlotReuseAtCap:
    """For each release path, drive to cap, exercise the path, then prove a fresh
    reservation succeeds — slot is provably reusable, not just eventually released."""

    def _fill_cap_for_user(self, owner: int) -> list[ImportJob]:
        jobs = []
        for _ in range(MAX_ACTIVE_PER_USER):
            j = reserve_slot(owner)
            assert j is not None
            jobs.append(j)
        assert reserve_slot(owner) is None, "Sanity: at cap"
        return jobs

    def test_slot_reuse_after_try_cancel_on_pending_upload(self):
        """Cancelling a PENDING_UPLOAD job releases the slot for a new reservation."""
        owner = 10
        jobs = self._fill_cap_for_user(owner)
        victim = jobs[0]
        assert victim.state == JobState.PENDING_UPLOAD

        # Cancel → transition to CANCELLED; then explicitly release capacity.
        victim.try_cancel()
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Slot must be reusable after PENDING_UPLOAD cancel"

    def test_slot_reuse_after_try_cancel_on_pending(self):
        """Cancelling a PENDING job releases the slot."""
        owner = 11
        jobs = self._fill_cap_for_user(owner)
        victim = jobs[0]
        victim.convert_to_pending()
        assert victim.state == JobState.PENDING

        victim.try_cancel()
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Slot must be reusable after PENDING cancel"

    def test_slot_reuse_after_worker_succeeds(self):
        """A SUCCEEDED job whose capacity is released frees the slot."""
        owner = 12
        jobs = self._fill_cap_for_user(owner)
        victim = jobs[0]
        victim.convert_to_pending()
        victim.try_start()

        success_msg = {"event": "complete", "data": {"message": "done"}}
        victim._finish(succeeded=True, terminal_msg=success_msg)
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Slot must be reusable after worker success"

    def test_slot_reuse_after_worker_fails(self):
        """A FAILED job whose capacity is released frees the slot."""
        owner = 13
        jobs = self._fill_cap_for_user(owner)
        victim = jobs[0]
        victim.convert_to_pending()
        victim.try_start()

        fail_msg = {"event": "error", "data": {"message": "parse error"}}
        victim._finish(succeeded=False, terminal_msg=fail_msg)
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Slot must be reusable after worker failure"

    def test_slot_reuse_after_worker_start_failure(self):
        """If the worker fails to start (job cancelled during PENDING), slot released."""
        owner = 14
        jobs = self._fill_cap_for_user(owner)
        victim = jobs[0]
        victim.convert_to_pending()
        # Simulate worker-start failure: cancel without starting, clean up.
        victim.try_cancel()
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Slot must be reusable after worker-start failure"

    def test_slot_reuse_after_parser_failure(self):
        """Parser failure (never converts to PENDING): cancel + release frees slot."""
        owner = 15
        jobs = self._fill_cap_for_user(owner)
        victim = jobs[0]
        # Still in PENDING_UPLOAD — parser error before convert_to_pending().
        victim.try_cancel()
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Slot must be reusable after parser failure"


# ---------------------------------------------------------------------------
# Cleanup-ordering: capacity NOT released before cleanup completes
# ---------------------------------------------------------------------------


class TestCleanupOrdering:
    def test_fresh_reservation_blocked_while_cleanup_pending(self, tmp_path):
        """Slot is still held (cap blocked) while temp files exist before release_capacity().

        Order required:
            publish terminal → cleanup_files() → release_capacity() [last]

        This test proves the capacity is NOT released until after cleanup completes.
        It does NOT actually defer cleanup; it checks the counter state directly
        before calling release_capacity() to prove no premature release.
        """
        owner = 20
        # Fill to cap.
        jobs = []
        for _ in range(MAX_ACTIVE_PER_USER):
            j = reserve_slot(owner)
            assert j is not None
            jobs.append(j)
        victim = jobs[0]
        victim.convert_to_pending()
        victim.try_start()

        # Publish terminal (step 1).
        victim._finish(
            succeeded=True,
            terminal_msg={"event": "complete", "data": {"message": "ok"}},
        )
        assert victim.is_terminal

        # Cleanup not yet called — capacity still held.
        assert victim._capacity_held, "Capacity must still be held before cleanup"
        assert reserve_slot(owner) is None, "Cap must block new slot before cleanup"

        # Cleanup (step 2) then release (step 3).
        victim.cleanup_files()
        victim.release_capacity()

        fresh = reserve_slot(owner)
        assert fresh is not None, "Cap must open after cleanup + release"


# ---------------------------------------------------------------------------
# Atomic reservation→job conversion
# ---------------------------------------------------------------------------


class TestAtomicConversion:
    def test_conversion_does_not_double_count_or_drop_slot(self):
        """convert_to_pending() must not transiently double-count or drop the slot.

        Before: 1 slot used (PENDING_UPLOAD).
        After:  1 slot used (PENDING).
        At no instant should the count be 0 or 2.
        """
        owner = 30
        job = reserve_slot(owner)
        assert job is not None

        counts_seen: list[int] = []

        def _monitor() -> None:
            # Sample rapidly during the conversion window.
            for _ in range(200):
                counts_seen.append(_active_count_for(owner))
                time.sleep(0.0001)

        t = threading.Thread(target=_monitor)
        t.start()

        # Do the conversion.
        job.convert_to_pending()

        t.join()

        # The count must always be exactly 1 (it was 1 before and must be 1 after).
        for c in counts_seen:
            assert c == 1, (
                f"Counter was {c} during conversion — double-count or slot drop detected"
            )

        # Verify final state.
        assert job.state == JobState.PENDING
        assert _active_count_for(owner) == 1

    def test_double_convert_is_a_noop(self):
        """Calling convert_to_pending() twice is safe — second call returns False."""
        owner = 31
        job = reserve_slot(owner)
        assert job is not None
        assert job.convert_to_pending() is True
        assert job.convert_to_pending() is False
        assert job.state == JobState.PENDING
        assert _active_count_for(owner) == 1


# ---------------------------------------------------------------------------
# Concurrent admission: several under-limit streams held simultaneously
# ---------------------------------------------------------------------------


class TestConcurrentAdmission:
    def test_concurrent_reservations_from_same_user_capped(self):
        """Concurrent calls from the same user cannot exceed per-user cap."""
        owner = 40
        results: list[ImportJob | None] = []
        lock = threading.Lock()

        def _try_reserve():
            j = reserve_slot(owner)
            with lock:
                results.append(j)

        threads = [
            threading.Thread(target=_try_reserve)
            for _ in range(MAX_ACTIVE_PER_USER + 3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        accepted = [r for r in results if r is not None]
        assert len(accepted) <= MAX_ACTIVE_PER_USER, (
            f"Per-user cap exceeded: {len(accepted)} > {MAX_ACTIVE_PER_USER}"
        )

    def test_aggregate_spool_bounded_by_caps(self):
        """Even with concurrent reservations, total active slots never exceed global cap."""
        results: list[ImportJob | None] = []
        lock = threading.Lock()

        def _try_reserve(user_id: int) -> None:
            j = reserve_slot(user_id)
            with lock:
                results.append(j)

        # Launch more threads than MAX_ACTIVE_GLOBAL.
        threads = [
            threading.Thread(target=_try_reserve, args=(i * 100,))
            for i in range(MAX_ACTIVE_GLOBAL + 5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        accepted = [r for r in results if r is not None]
        assert len(accepted) <= MAX_ACTIVE_GLOBAL, (
            f"Global cap exceeded: {len(accepted)} > {MAX_ACTIVE_GLOBAL}"
        )


# ---------------------------------------------------------------------------
# No-duplicate-on-retry (run_txn idempotent units)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunTxnIdempotency:
    """run_txn must replay on SQLite contention and produce exactly one row.

    These tests use a real temporary database (via init_database) so
    run_txn's own session_scope() calls land in the same file as the
    assertions.
    """

    async def test_no_duplicate_session_on_import_chunk_retry(self, temp_db):
        """Fault-injected contention on import chunk write: run_txn retries and
        exactly one Session row is persisted (UNIQUE guard prevents duplication).

        Strategy: the unit_of_work raises on the first call (simulating SQLite
        contention), _is_sqlite_contention is patched to return True, so run_txn
        retries.  The second call succeeds.  After two attempts exactly one row
        exists — the UNIQUE(device_id, device_session_id) constraint makes the
        write idempotent.
        """
        import uuid

        from unittest.mock import patch

        from sqlalchemy import select

        from snore.database.models import Device, Profile, Session, User
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )
        from snore.database.txn import run_txn

        db_path = str(temp_db)
        await init_database(db_path)

        # Seed: user → profile → device.
        async with session_scope() as db:
            user = User(
                canonical_email=f"runtxn_{uuid.uuid4().hex[:8]}@test", role="admin"
            )
            db.add(user)
            await db.flush()
            profile = Profile(user_id=user.id, name="Default")
            db.add(profile)
            await db.flush()
            device = Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="CPAP",
                serial_number=f"SN_{uuid.uuid4().hex[:6]}",
            )
            db.add(device)
            await db.flush()
            device_id = device.id

        # unit_of_work: raises on attempt 1 (contention), succeeds on attempt 2.
        device_session_id = f"txn_test_{uuid.uuid4().hex[:8]}"
        attempt_count = 0

        async def _insert_session(db):
            nonlocal attempt_count
            attempt_count += 1
            # Simulate SQLite contention on the first attempt.
            if attempt_count == 1:
                raise RuntimeError("SQLITE_BUSY simulated")

            from datetime import UTC, datetime

            sess = Session(
                device_id=device_id,
                device_session_id=device_session_id,
                start_time=datetime(2025, 1, 1, 22, 0, 0, tzinfo=UTC),
                end_time=datetime(2025, 1, 2, 6, 0, 0, tzinfo=UTC),
                duration_seconds=28800.0,
            )
            db.add(sess)
            await db.flush()
            return True

        # _is_sqlite_contention always returns True; asyncio.sleep patched out.
        with (
            patch("snore.database.txn._is_sqlite_contention", return_value=True),
            patch("snore.database.txn.asyncio.sleep"),
        ):
            await run_txn(_insert_session)

        assert attempt_count == 2, (
            f"Expected 2 attempts (1 contention + 1 success); got {attempt_count}"
        )

        # Exactly one row persisted.
        async with session_scope() as db:
            rows = (
                (
                    await db.execute(
                        select(Session).where(
                            Session.device_session_id == device_session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1, f"Expected 1 Session row after retry; got {len(rows)}"

        await cleanup_database()

    async def test_non_contention_exception_propagates_immediately(self, temp_db):
        """run_txn does not retry on non-contention errors; the error propagates."""
        from snore.database.session import cleanup_database, init_database
        from snore.database.txn import run_txn

        await init_database(str(temp_db))

        call_count = 0

        async def _failing(db):
            nonlocal call_count
            call_count += 1
            raise ValueError("not a contention error")

        with pytest.raises(ValueError, match="not a contention error"):
            await run_txn(_failing)

        assert call_count == 1, "Non-contention error must not be retried"

        await cleanup_database()

    async def test_exhausted_contention_raises_last_error(self, temp_db):
        """run_txn raises after max_attempts of contention; no infinite loop."""
        from unittest.mock import patch

        from snore.database.session import cleanup_database, init_database
        from snore.database.txn import run_txn

        await init_database(str(temp_db))

        call_count = 0

        async def _always_contention(db):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("SQLITE_BUSY simulated")

        with patch("snore.database.txn._is_sqlite_contention", return_value=True):
            with pytest.raises(RuntimeError, match="SQLITE_BUSY simulated"):
                await run_txn(_always_contention, max_attempts=3)

        assert call_count == 3, f"Expected exactly 3 attempts; got {call_count}"

        await cleanup_database()
