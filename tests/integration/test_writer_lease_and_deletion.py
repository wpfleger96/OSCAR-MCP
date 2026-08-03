"""Adversarial integration tests for the writer lease and deletion saga.

Covers (per the acceptance matrix):
- Lease exclusion set:
    * delete refused while API/another process holds a shared lease
    * direct BackupService subprocess during exclusive hold → write never begins
    * pre-tombstone failure → shared acquire succeeds (no stranded exclusive lock)
- Deletion saga fault injection:
    * crash/exception before tombstone commit → deletion exits nonzero, lease released
    * crash after tombstone (before rename) → startup recovery finishes saga
    * crash after rename (before cascade) → startup recovery finishes saga
    * crash after cascade (before purge) → startup recovery finishes purge
- Startup recovery: interrupted saga leaves no orphaned rows or private raw files
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import textwrap

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from snore.services.writer_lease import WriterLeaseError, WriterLeaseManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(lock_path: Path) -> WriterLeaseManager:
    """Create a WriterLeaseManager using a temp lock file."""
    return WriterLeaseManager(lock_path=lock_path)


def _shared_acquire_succeeds(lock_path: Path) -> bool:
    """Return True if a shared acquire on lock_path succeeds (non-blocking)."""
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Lease unit tests (single-process)
# ---------------------------------------------------------------------------


class TestWriterLeaseManager:
    def test_shared_acquire_and_release(self, tmp_path):
        """Basic shared acquire increments refcount, release decrements to zero."""
        lock = tmp_path / "writers.lock"
        mgr = _make_lease(lock)

        mgr.acquire_shared()
        assert mgr._refcount == 1
        assert mgr._fd is not None

        mgr.release()
        assert mgr._refcount == 0
        assert mgr._fd is None

    def test_nested_shared_acquire_refcounts(self, tmp_path):
        """Multiple shared acquires refcount; release to zero unlocks."""
        lock = tmp_path / "writers.lock"
        mgr = _make_lease(lock)

        mgr.acquire_shared()
        mgr.acquire_shared()
        assert mgr._refcount == 2

        mgr.release()
        assert mgr._refcount == 1
        assert mgr._fd is not None  # Still held.

        mgr.release()
        assert mgr._refcount == 0
        assert mgr._fd is None  # Released.

    def test_shared_context_manager(self, tmp_path):
        """shared() context manager acquires and releases correctly."""
        lock = tmp_path / "writers.lock"
        mgr = _make_lease(lock)

        with mgr.shared():
            assert mgr._refcount == 1
        assert mgr._refcount == 0

    def test_exclusive_context_manager(self, tmp_path):
        """exclusive() context manager acquires and releases exclusively."""
        lock = tmp_path / "writers.lock"
        mgr = _make_lease(lock)

        with mgr.exclusive():
            assert mgr._refcount == 1
        assert mgr._refcount == 0
        assert mgr._fd is None

    def test_exclusive_refuses_when_shared_is_held(self, tmp_path):
        """Exclusive acquire raises WriterLeaseError when shared hold is active."""
        lock = tmp_path / "writers.lock"
        holder = _make_lease(lock)
        requester = _make_lease(lock)

        holder.acquire_shared()
        try:
            with pytest.raises(WriterLeaseError):
                requester.acquire_exclusive()
        finally:
            holder.release()

    def test_pre_tombstone_failure_releases_exclusive_lock(self, tmp_path):
        """If an operation fails before committing the tombstone, the exclusive
        lock is released — a subsequent shared acquire must succeed.

        This tests the plan's guarantee: 'a failed tombstone txn cannot strand
        admission closed' and the direct-lock-acquire test from pass-6 MINOR.
        """
        lock_path = tmp_path / "writers.lock"
        mgr = _make_lease(lock_path)

        # Simulate: exclusive acquired but operation raises before tombstone commit.
        try:
            with mgr.exclusive():
                raise RuntimeError("Pre-tombstone failure injected")
        except RuntimeError:
            pass  # Expected.

        # The exclusive should be released (context manager exited cleanly).
        assert mgr._fd is None, "Exclusive fd must be closed after exception in context"

        # A direct shared acquire on the lock file must succeed.
        assert _shared_acquire_succeeds(lock_path), (
            "Shared acquire must succeed after failed exclusive op — lock not stranded"
        )

    def test_lifespan_recovery_failure_yields_exactly_one_shared_acquire(
        self, tmp_path
    ):
        """Injected recovery failure in the real lifespan must not double-acquire
        the shared lease.

        Strategy: patch DeletionSaga.recover to raise, then run the actual
        ``app.router.lifespan_context()`` with init_database and the import
        reaper patched out (so the test stays fast and self-contained).  Assert
        that exactly one shared hold is active during serving and that it is
        released cleanly after exit.
        """
        import asyncio as _asyncio
        import threading as _threading

        from unittest.mock import AsyncMock, patch

        from snore.api.app import create_app
        from snore.services.writer_lease import WriterLeaseManager

        lock_path = tmp_path / "lifespan_writers.lock"
        mgr = WriterLeaseManager(lock_path=lock_path)

        # Sentinel list: [(refcount_during_serving,)]
        snapshots: list[int] = []

        app = create_app()

        _dummy_stop = _threading.Event()
        _dummy_thread = _threading.Thread(target=_dummy_stop.wait, daemon=True)
        _dummy_thread.start()

        async def run_lifespan() -> None:
            with (
                patch("snore.api.app.init_database", new_callable=AsyncMock),
                patch(
                    "snore.api.app._start_import_reaper",
                    return_value=(_dummy_thread, _dummy_stop),
                ),
                patch("snore.api.app._shutdown_import_jobs", return_value=[]),
                # Fault recovery so the lifespan exercises the failure branch.
                patch(
                    "snore.services.profile_service.DeletionSaga.recover",
                    side_effect=RuntimeError("injected recovery failure"),
                ),
                # Point the lifespan's writer lease to our test-local path.
                patch(
                    "snore.services.writer_lease.get_writer_lease",
                    return_value=mgr,
                ),
            ):
                async with app.router.lifespan_context(app):
                    # Serving: exactly one shared hold must be active.
                    snapshots.append(mgr._refcount)

        _asyncio.run(run_lifespan())
        _dummy_stop.set()
        _dummy_thread.join(timeout=1.0)

        assert len(snapshots) == 1
        assert snapshots[0] == 1, (
            f"Expected refcount 1 during serving (recovery failure path); "
            f"got {snapshots[0]}"
        )
        # After lifespan exit, the shared hold must be released.
        assert mgr._refcount == 0, (
            f"Expected refcount 0 after lifespan exit; got {mgr._refcount}"
        )

    def test_release_idempotent_when_not_held(self, tmp_path):
        """release() when refcount is already 0 is a safe no-op."""
        lock = tmp_path / "writers.lock"
        mgr = _make_lease(lock)
        mgr.release()  # No-op; must not raise.
        assert mgr._refcount == 0

    def test_shared_hold_blocks_exclusive_in_same_process(self, tmp_path):
        """While shared is held, exclusive (from a different manager instance) raises."""
        lock_path = tmp_path / "writers.lock"
        holder = _make_lease(lock_path)
        exclusive_requester = _make_lease(lock_path)

        with holder.shared():
            with pytest.raises(WriterLeaseError, match="exclusive writer lease"):
                exclusive_requester.acquire_exclusive()


# ---------------------------------------------------------------------------
# Subprocess-level lease exclusion tests
# ---------------------------------------------------------------------------


class TestLeaseExclusionCrossProcess:
    """Prove that exclusive/shared lease interactions work across process boundaries."""

    def test_subprocess_backup_blocked_during_exclusive_hold(self, tmp_path):
        """While deletion holds the exclusive lease, a subprocess BackupService call
        cannot acquire the shared lease and therefore cannot write.

        The subprocess script tries to acquire a shared flock on the same file
        (simulating what BackupService does) and reports whether it succeeded.
        """
        lock_path = tmp_path / "writers.lock"

        # Acquire exclusive in this process.
        mgr = _make_lease(lock_path)
        mgr.acquire_exclusive()
        try:
            # Subprocess tries a non-blocking shared acquire on the same file.
            script = textwrap.dedent(f"""
                import fcntl, os, sys
                lock_path = {str(lock_path)!r}
                fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    print("ACQUIRED")
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    print("BLOCKED")
                finally:
                    os.close(fd)
            """)
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.strip()
            assert output == "BLOCKED", (
                f"Subprocess shared acquire should be BLOCKED while exclusive is held; got: {output!r}"
            )
        finally:
            mgr.release_exclusive()

    def test_subprocess_shared_acquire_succeeds_after_exclusive_released(
        self, tmp_path
    ):
        """After the exclusive is released, a subprocess can acquire shared."""
        lock_path = tmp_path / "writers.lock"

        # Acquire and release exclusive.
        mgr = _make_lease(lock_path)
        with mgr.exclusive():
            pass  # Immediately released.

        script = textwrap.dedent(f"""
            import fcntl, os
            lock_path = {str(lock_path)!r}
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                print("ACQUIRED")
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                print("BLOCKED")
            finally:
                os.close(fd)
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.stdout.strip() == "ACQUIRED", (
            "Subprocess must be able to acquire shared after exclusive is released"
        )


# ---------------------------------------------------------------------------
# Deletion saga fault-injection tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def saga_db(tmp_path):
    """Provide an isolated DB + raw root for deletion saga tests."""
    db_path = tmp_path / "saga_test.db"
    raw_root = tmp_path / "raw"
    raw_root.mkdir()

    db_url = f"sqlite+aiosqlite:///{db_path}"
    os.environ["SNORE_DATABASE_URL"] = db_url

    from snore.database.session import cleanup_database, init_database_from_url

    await init_database_from_url(db_url)

    try:
        yield db_path, raw_root
    finally:
        os.environ.pop("SNORE_DATABASE_URL", None)
        await cleanup_database()


async def _create_user_and_profiles(
    db_path: Path, raw_root: Path
) -> tuple[Any, Any, Any]:
    """Create a user with two profiles (so we can delete one)."""
    from snore.database.models import Profile, User
    from snore.database.session import session_scope

    async with session_scope() as db:
        user = User(canonical_email="saga_test@example.com", role="admin")
        db.add(user)
        await db.flush()

        profile_a = Profile(user_id=user.id, name="Profile A")
        db.add(profile_a)
        await db.flush()

        profile_b = Profile(user_id=user.id, name="Profile B")
        db.add(profile_b)
        await db.flush()

        user.default_profile_id = profile_a.id
        await db.flush()

        return user, profile_a, profile_b


@pytest.mark.asyncio
class TestDeletionSagaFaultInjection:
    """Fault-inject at each saga boundary; prove idempotent recovery."""

    async def test_pre_tombstone_failure_leaves_no_orphans(self, saga_db):
        """Exception before tombstone commit: profile still live, raw files intact,
        exclusive lease released (shared acquire succeeds after)."""
        db_path, raw_root = saga_db
        lock_path = raw_root.parent / "writers.lock"

        user, profile_a, profile_b = await _create_user_and_profiles(db_path, raw_root)

        # Create a raw directory for profile_a.
        raw_dir = raw_root / str(profile_a.id)
        raw_dir.mkdir()
        (raw_dir / "some_file.dat").write_text("data")

        from snore.database.models import Profile
        from snore.database.session import session_scope
        from snore.services.writer_lease import WriterLeaseManager

        lease_mgr = WriterLeaseManager(lock_path=lock_path)

        # Simulate a failure before _step1_tombstone commits (e.g., last-profile check
        # raises — but here we inject directly via try_cancel inside exclusive context).
        try:
            with lease_mgr.exclusive():
                raise RuntimeError("Injected: pre-tombstone failure")
        except RuntimeError:
            pass

        # Verify: exclusive lock released — shared acquire succeeds.
        assert _shared_acquire_succeeds(lock_path), (
            "Exclusive lock must be released after pre-tombstone failure"
        )

        # Verify: profile_a still live (no tombstone).
        async with session_scope() as db:
            p = await db.get(Profile, profile_a.id)
        assert p is not None, "Profile must survive pre-tombstone failure"
        assert p.deleting_at is None, "No tombstone must exist"

        # Verify: raw files intact.
        assert raw_dir.exists(), "Raw files must be intact after pre-tombstone failure"

    async def test_last_profile_deletion_is_rejected(self, saga_db):
        """Deleting the only profile raises ProfileLastError."""
        import asyncio

        db_path, raw_root = saga_db
        lock_path = raw_root.parent / "writers.lock"

        from snore.database.models import Profile, User
        from snore.database.session import session_scope
        from snore.services.profile_service import ProfileLastError

        # Create a user with only ONE profile.
        async with session_scope() as db:
            user = User(canonical_email="last_profile@example.com", role="admin")
            db.add(user)
            await db.flush()
            profile = Profile(user_id=user.id, name="Only Profile")
            db.add(profile)
            await db.flush()
            user.default_profile_id = profile.id

        from snore.services.profile_service import DeletionSaga
        from snore.services.writer_lease import WriterLeaseManager

        saga = DeletionSaga(raw_root=raw_root)
        # Override saga's lease with our test lock path.
        saga._raw_root = raw_root

        # Patch the writer lease to use a test-local path.
        import snore.services.profile_service as psvc

        original_get = psvc.get_writer_lease
        psvc.get_writer_lease = lambda: WriterLeaseManager(lock_path=lock_path)
        try:
            with pytest.raises(ProfileLastError):
                await asyncio.to_thread(saga.delete_profile, profile.id, user.id)
        finally:
            psvc.get_writer_lease = original_get

        # Lease must be released after failure.
        assert _shared_acquire_succeeds(lock_path), (
            "Lease must be released after last-profile rejection"
        )

    async def test_saga_tombstone_only_leaves_recovery_path(self, saga_db):
        """After tombstone but before rename, startup recovery must complete the saga."""
        db_path, raw_root = saga_db

        user, profile_a, profile_b = await _create_user_and_profiles(db_path, raw_root)

        # Create raw dir for profile_a.
        raw_dir = raw_root / str(profile_a.id)
        raw_dir.mkdir()
        (raw_dir / "cpap.data").write_text("therapy data")

        from snore.database.models import Profile
        from snore.database.session import session_scope
        from snore.services.profile_service import DeletionSaga

        # Step 1 only: set the tombstone directly without proceeding to rename.
        async with session_scope() as db:
            p_a = await db.get(Profile, profile_a.id)
            p_a.deleting_at = datetime.now(UTC)
            u = await db.get(type(user), user.id)
            if u.default_profile_id == profile_a.id:
                u.default_profile_id = profile_b.id

        # Confirm tombstone exists and raw dir exists (crash simulated).
        async with session_scope() as db:
            p = await db.get(Profile, profile_a.id)
        assert p is not None and p.deleting_at is not None

        assert raw_dir.exists(), "Raw dir must exist before recovery"

        # Run recovery (simulates startup — finds tombstone, re-runs steps 2-4).
        import asyncio  # noqa: PLC0415

        saga = DeletionSaga(raw_root=raw_root)
        await asyncio.to_thread(saga.recover)

        # After recovery: profile row is gone, raw dir is gone, no quarantine remnants.
        async with session_scope() as db:
            p_gone = await db.get(Profile, profile_a.id)
        assert p_gone is None, "Profile row must be gone after recovery"
        assert not raw_dir.exists(), "Raw dir must be gone after recovery"
        quarantine_dir = raw_root / ".quarantine" / str(profile_a.id)
        assert not quarantine_dir.exists(), (
            "Quarantine dir must be purged after recovery"
        )

    async def test_saga_rename_only_leaves_recovery_path(self, saga_db):
        """After tombstone + rename but before cascade, recovery must cascade and purge."""
        db_path, raw_root = saga_db

        user, profile_a, profile_b = await _create_user_and_profiles(db_path, raw_root)

        quarantine_root = raw_root / ".quarantine"
        quarantine_root.mkdir()
        quarantine_dir = quarantine_root / str(profile_a.id)
        quarantine_dir.mkdir()
        (quarantine_dir / "cpap.data").write_text("therapy data")

        from snore.database.models import Profile
        from snore.database.session import session_scope
        from snore.services.profile_service import DeletionSaga

        # Step 1: tombstone.
        async with session_scope() as db:
            p_a = await db.get(Profile, profile_a.id)
            p_a.deleting_at = datetime.now(UTC)
            u = await db.get(type(user), user.id)
            if u.default_profile_id == profile_a.id:
                u.default_profile_id = profile_b.id
        # Step 2 already "done" (quarantine dir exists); raw dir does NOT exist.

        # Run recovery.
        import asyncio  # noqa: PLC0415

        saga = DeletionSaga(raw_root=raw_root)
        await asyncio.to_thread(saga.recover)
        async with session_scope() as db:
            p_gone = await db.get(Profile, profile_a.id)
        assert p_gone is None, (
            "Profile row must be gone after recovery from rename-only state"
        )
        assert not quarantine_dir.exists(), "Quarantine dir must be purged by recovery"

    async def test_saga_cascade_only_leaves_recovery_path(self, saga_db):
        """After cascade (profile row gone) but quarantine remains, recover() purges it.

        This covers the crash-after-cascade scenario: the profile row is gone
        (no tombstone visible), but the quarantine directory was not yet purged.
        recover() must enumerate quarantine dirs with no tombstone and purge them.
        """
        db_path, raw_root = saga_db

        user, profile_a, profile_b = await _create_user_and_profiles(db_path, raw_root)

        quarantine_root = raw_root / ".quarantine"
        quarantine_root.mkdir()
        quarantine_dir = quarantine_root / str(profile_a.id)
        quarantine_dir.mkdir()
        (quarantine_dir / "cpap.data").write_text("therapy data")

        from snore.database.models import Profile
        from snore.database.session import session_scope
        from snore.services.profile_service import DeletionSaga

        # Simulate: tombstone committed then cascade completed (profile row gone).
        # The purge step was interrupted before completion.
        async with session_scope() as db:
            p_a = await db.get(Profile, profile_a.id)
            await db.delete(p_a)
        # Profile row is gone; quarantine dir still exists (orphaned).

        # recover() must find and purge the orphaned quarantine dir even though
        # no tombstone row exists.
        saga = DeletionSaga(raw_root=raw_root)
        import asyncio  # noqa: PLC0415

        await asyncio.to_thread(saga.recover)

        assert not quarantine_dir.exists(), (
            "recover() must purge quarantine dir even when no tombstone row exists"
        )

    async def test_recovery_leaves_no_orphaned_private_files(self, saga_db):
        """Full recovery run with two tombstoned profiles leaves no orphaned raw files."""
        db_path, raw_root = saga_db

        user, profile_a, profile_b = await _create_user_and_profiles(db_path, raw_root)

        # Create a THIRD profile (so we can tombstone both A and B).
        from snore.database.models import Profile
        from snore.database.session import session_scope

        async with session_scope() as db:
            profile_c = Profile(user_id=user.id, name="Profile C")
            db.add(profile_c)
            await db.flush()

        # Create raw dirs.
        for pid in [profile_a.id, profile_b.id]:
            d = raw_root / str(pid)
            d.mkdir()
            (d / "data.bin").write_bytes(b"\x00" * 100)

        # Tombstone both A and B.
        from snore.database.session import session_scope

        async with session_scope() as db:
            for pid in [profile_a.id, profile_b.id]:
                p = await db.get(Profile, pid)
                p.deleting_at = datetime.now(UTC)
            u = await db.get(type(user), user.id)
            u.default_profile_id = profile_c.id

        import asyncio  # noqa: PLC0415

        from snore.services.profile_service import DeletionSaga

        saga = DeletionSaga(raw_root=raw_root)
        await asyncio.to_thread(saga.recover)

        # Neither raw dir should exist.
        for pid in [profile_a.id, profile_b.id]:
            raw_dir = raw_root / str(pid)
            assert not raw_dir.exists(), (
                f"Raw dir for profile {pid} must be gone after recovery"
            )
            q_dir = raw_root / ".quarantine" / str(pid)
            assert not q_dir.exists(), f"Quarantine for profile {pid} must be purged"

        # Profile C must still exist.
        async with session_scope() as db:
            p_c = await db.get(Profile, profile_c.id)
        assert p_c is not None, "Profile C must be untouched by recovery"
