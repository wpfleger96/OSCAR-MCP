"""Integration tests for stale-DB detection (inode swap) and StaticRuntime profile refresh.

Covers the contract added in snore.database.session:
- ``check_db_staleness()`` — detects inode change, rebuilds engine transparently.
- ``get_engine_generation()`` — monotonically increments on each successful init.

And in snore.mcp.server:
- ``StaticRuntime(base_scope_provider, profile_id)`` — re-resolves profile_id when
  the engine generation changes since construction.
"""

from __future__ import annotations

import os
import unittest.mock

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sqlalchemy import select, text

import snore.database.session as sess_mod

from snore.database.models import Profile, User
from snore.database.session import (
    cleanup_database,
    get_engine_generation,
    init_database,
    session_scope,
)
from snore.mcp.server import StaticRuntime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_db_with_profile(db_path: Path, email: str, profile_name: str) -> int:
    """Init a fresh DB, insert a User+Profile, commit, dispose. Returns profile id."""
    await init_database(str(db_path))
    profile_id: int
    async with session_scope() as db:
        user = User(canonical_email=email, role="admin")
        db.add(user)
        await db.flush()
        profile = Profile(user_id=user.id, name=profile_name)
        db.add(profile)
        await db.flush()
        profile_id = profile.id
    await cleanup_database()
    return profile_id


# ---------------------------------------------------------------------------
# Test: inode swap triggers engine rebuild and serves new data
# ---------------------------------------------------------------------------


async def test_db_file_swap_reinitializes_engine_and_serves_new_data(
    tmp_path: Path,
) -> None:
    """Atomically swapping the DB file causes session_scope to rebuild the engine.

    Arrange: DB A has "profile-a"; DB B has "profile-b".
    Act:     init on A, replace A with B, open a session_scope.
    Assert:  B's profile visible, A's profile absent, engine object replaced,
             engine generation incremented.
    """
    path_a = tmp_path / "a.db"
    path_b = tmp_path / "b.db"

    # Build each DB independently; both are cleanly closed before the swap.
    await _build_db_with_profile(path_a, "a@example.com", "profile-a")
    await _build_db_with_profile(path_b, "b@example.com", "profile-b")

    # Re-init pointing at A; record engine reference and generation counter.
    await init_database(str(path_a))
    engine_before = sess_mod._engine
    gen_before = get_engine_generation()

    # Atomic file replacement: path_a now has B's inode and content.
    os.replace(path_b, path_a)

    # session_scope detects the inode mismatch and rebuilds before yielding.
    async with session_scope() as db:
        profiles = (await db.execute(select(Profile))).scalars().all()
    names = {p.name for p in profiles}

    assert "profile-b" in names, "DB B's profile must be visible after swap"
    assert "profile-a" not in names, "DB A's profile must not appear after swap"
    assert sess_mod._engine is not engine_before, (
        "Engine object must be replaced after inode swap"
    )
    assert get_engine_generation() > gen_before, (
        "Engine generation must increment after successful rebuild"
    )


# ---------------------------------------------------------------------------
# Test: unchanged inode leaves engine untouched
# ---------------------------------------------------------------------------


async def test_same_inode_does_not_rebuild_engine(tmp_path: Path) -> None:
    """When the DB file's inode is unchanged, session_scope does not rebuild the engine.

    Arrange: init a DB.
    Act:     open a session_scope and execute a trivial query.
    Assert:  engine object identity is preserved.
    """
    db_path = tmp_path / "same.db"
    await init_database(str(db_path))
    engine_before = sess_mod._engine

    async with session_scope() as db:
        await db.execute(text("SELECT 1"))

    assert sess_mod._engine is engine_before, (
        "Engine must not be rebuilt when inode is unchanged"
    )


# ---------------------------------------------------------------------------
# Test: OSError from stat is silently absorbed
# ---------------------------------------------------------------------------


async def test_stat_failure_is_transient_and_keeps_engine(tmp_path: Path) -> None:
    """An OSError from os.stat inside check_db_staleness is silently swallowed.

    The session must succeed and the engine must not be replaced.
    After the patch is removed, operation returns to normal.
    """
    db_path = tmp_path / "stat_fail.db"
    await init_database(str(db_path))
    engine_before = sess_mod._engine

    # Patch os.stat so every call raises; session must still work.
    with unittest.mock.patch(
        "snore.database.session.os.stat", side_effect=OSError("boom")
    ):
        async with session_scope() as db:
            result = (await db.execute(text("SELECT 1"))).scalar()

    assert result == 1, "Session must succeed even when os.stat raises OSError"
    assert sess_mod._engine is engine_before, (
        "Engine must not change when stat raised OSError"
    )

    # After unpatching, normal operation resumes with the same engine.
    async with session_scope() as db:
        result2 = (await db.execute(text("SELECT 1"))).scalar()

    assert result2 == 1, "Session must work normally once stat patch is removed"
    assert sess_mod._engine is engine_before, (
        "Engine must still be unchanged after a normal post-patch session"
    )


# ---------------------------------------------------------------------------
# Test: StaticRuntime re-resolves profile_id when engine generation changes
# ---------------------------------------------------------------------------


async def test_static_runtime_refreshes_profile_id_after_swap(tmp_path: Path) -> None:
    """StaticRuntime updates profile_id after an inode swap changes the engine generation.

    Arrange:
      - DB A: one live profile (id=1).
      - DB B: one tombstoned profile (id=1) + one live profile (id=2).
        The first live profile ordered by id in B is therefore id=2.
    Act:
      - Re-init pointing at A; build StaticRuntime with A's profile_id.
      - Atomically replace A with B.
      - Enter runtime.scope_provider() — triggers inode check, engine rebuild,
        and profile re-resolution inside the open scope.
    Assert:
      - runtime.profile_id == B's first live profile id (2).
    """
    path_a = tmp_path / "rt_a.db"
    path_b = tmp_path / "rt_b.db"

    # DB A: one live profile.
    profile_a_id = await _build_db_with_profile(
        path_a, "rta@example.com", "rt-profile-a"
    )

    # DB B: tombstoned profile (id=1) + live profile (id=2).
    await init_database(str(path_b))
    async with session_scope() as db:
        user_b = User(canonical_email="rtb@example.com", role="admin")
        db.add(user_b)
        await db.flush()

        # Tombstoned — invisible to live-profile queries.
        profile_b_dead = Profile(
            user_id=user_b.id,
            name="rt-profile-b-dead",
            deleting_at=datetime.now(UTC),
        )
        db.add(profile_b_dead)
        await db.flush()

        # Live — this is the first live profile by id in DB B.
        profile_b_live = Profile(user_id=user_b.id, name="rt-profile-b-live")
        db.add(profile_b_live)
        await db.flush()
        profile_b_live_id = profile_b_live.id
    await cleanup_database()

    # Sanity: B's live profile must have a higher id than A's (1 vs 2).
    assert profile_b_live_id != profile_a_id, (
        "Test setup error: B's live profile_id must differ from A's so the "
        f"assertion below is non-trivial (got profile_a_id={profile_a_id}, "
        f"profile_b_live_id={profile_b_live_id})"
    )

    # Re-init pointing at DB A; bind runtime to A's profile.
    await init_database(str(path_a))
    runtime = StaticRuntime(base_scope_provider=session_scope, profile_id=profile_a_id)

    # Swap: path_a now holds B's content with B's inode.
    os.replace(path_b, path_a)

    # scope_provider triggers the staleness check (engine rebuild) and
    # re-resolves profile_id to B's first live profile inside the scope.
    async with runtime.scope_provider() as _db:
        pass  # side effect: engine rebuilt, profile_id updated

    assert runtime.profile_id == profile_b_live_id, (
        f"After swap, StaticRuntime must re-resolve to B's first live profile "
        f"(expected {profile_b_live_id}, got {runtime.profile_id})"
    )


# ---------------------------------------------------------------------------
# Test: swap while a session is already open
# ---------------------------------------------------------------------------


async def test_swap_with_open_session_keeps_inflight_session_working(
    tmp_path: Path,
) -> None:
    """Swapping the DB file while a session is open does not break inflight sessions.

    Arrange: DB A ("profile-a"), DB B ("profile-b").
    Act:     init on A, open s1, verify A visible, swap A→B inside s1, open s2
             (triggers inode detection + engine rebuild), verify s2 sees B, then
             query s1 again (its file descriptor still holds A's inode), exit s1,
             verify a fresh session sees B and the engine was replaced.
    Assert:
      - s1 sees A's profile before the swap.
      - s2 (opened after the swap) sees B's profile.
      - s1's inflight connection still queries successfully after the swap and
        sees A's data (open fd keeps the old inode alive; dispose() does not
        close checked-out connections).
      - After s1 exits, a fresh session_scope sees B and the engine is replaced.

    NOTE: if step 4 errors instead (driver-level fd behaviour differs from
    POSIX expectations on this platform), the test re-raises with a message
    flagging this as a product finding rather than silently masking the failure.
    """
    path_a = tmp_path / "sw_a.db"
    path_b = tmp_path / "sw_b.db"

    await _build_db_with_profile(path_a, "swa@example.com", "profile-a")
    await _build_db_with_profile(path_b, "swb@example.com", "profile-b")

    await init_database(str(path_a))
    engine_before_swap = sess_mod._engine

    async with session_scope() as s1:
        # s1 is connected to DB A.
        names_before = {
            p.name for p in (await s1.execute(select(Profile))).scalars().all()
        }
        assert "profile-a" in names_before, "s1 must see DB A before swap"

        # Atomically replace path_a with DB B.
        os.replace(path_b, path_a)

        # s2: opening triggers inode check → engine rebuild → new connection to DB B.
        async with session_scope() as s2:
            names_s2 = {
                p.name for p in (await s2.execute(select(Profile))).scalars().all()
            }
        assert "profile-b" in names_s2, "s2 must see DB B after swap"
        assert "profile-a" not in names_s2, "s2 must not see DB A"

        # s1's connection is checked-out and was not closed by dispose().
        # Its open fd still points to DB A's inode.
        try:
            names_after_swap = {
                p.name for p in (await s1.execute(select(Profile))).scalars().all()
            }
            assert "profile-a" in names_after_swap, (
                "s1's inflight connection must still see DB A after the swap "
                "(open fd keeps the old inode alive)"
            )
        except Exception as exc:
            raise AssertionError(
                f"Product finding: s1 query failed after swap "
                f"(SQLite fd behaviour differs from POSIX expectation): {exc}"
            ) from exc

    # After both contexts exit, a fresh session uses the rebuilt engine (DB B).
    async with session_scope() as fresh:
        names_fresh = {
            p.name for p in (await fresh.execute(select(Profile))).scalars().all()
        }

    assert "profile-b" in names_fresh, "Fresh session must see DB B after swap"
    assert "profile-a" not in names_fresh, "Fresh session must not see DB A"
    assert sess_mod._engine is not engine_before_swap, (
        "Engine object must have been replaced by the swap detection"
    )


# ---------------------------------------------------------------------------
# Test: StaticRuntime retries profile resolution until a live profile appears
# ---------------------------------------------------------------------------


async def test_static_runtime_retries_profile_resolution_until_found(
    tmp_path: Path,
) -> None:
    """StaticRuntime retries profile resolution on each scope entry after a swap with no live profile.

    Arrange:
      - DB A: one live profile (id captured).
      - DB B: only a tombstoned profile (no live profiles).
    Act:
      - Re-init pointing at A; construct StaticRuntime with A's profile_id.
      - Replace A with B (no live profiles in new DB).
      - Enter scope_provider() once: detects generation change, finds no live
        profile, retains A's profile_id, and does NOT advance _known_generation.
      - Insert a live profile into DB B (now at path_a).
      - Enter scope_provider() again: _known_generation still differs from
        current_gen, so resolution is retried and finds the new profile.
    Assert:
      - After first scope entry: runtime.profile_id == profile_a_id (unchanged).
      - After second scope entry: runtime.profile_id == newly inserted profile's id.
    """
    path_a = tmp_path / "retry_a.db"
    path_b = tmp_path / "retry_b.db"

    # DB A: one live profile.
    profile_a_id = await _build_db_with_profile(path_a, "rya@example.com", "retry-a")

    # DB B: only a tombstoned profile — no live profiles.
    await init_database(str(path_b))
    async with session_scope() as db:
        user_b = User(canonical_email="ryb@example.com", role="admin")
        db.add(user_b)
        await db.flush()
        tombstoned = Profile(
            user_id=user_b.id,
            name="retry-b-dead",
            deleting_at=datetime.now(UTC),
        )
        db.add(tombstoned)
        await db.flush()
    await cleanup_database()

    # Re-init pointing at DB A; bind runtime to A's profile.
    await init_database(str(path_a))
    runtime = StaticRuntime(base_scope_provider=session_scope, profile_id=profile_a_id)

    # Swap: path_a now holds DB B's content (no live profiles).
    os.replace(path_b, path_a)

    # First scope entry: detects generation change, finds nothing, retains profile_id.
    async with runtime.scope_provider() as _db:
        pass

    assert runtime.profile_id == profile_a_id, (
        "After swap to a DB with no live profiles, profile_id must be retained "
        f"(expected {profile_a_id}, got {runtime.profile_id})"
    )

    # Insert a live profile into the swapped-in DB (DB B at path_a).
    new_profile_id: int
    async with session_scope() as db:
        user_b_row = (await db.execute(select(User))).scalars().first()
        assert user_b_row is not None, "DB B must have a user"
        live_profile = Profile(user_id=user_b_row.id, name="retry-b-live")
        db.add(live_profile)
        await db.flush()
        new_profile_id = live_profile.id

    # Second scope entry: _known_generation still differs (was not advanced
    # in the first entry), so resolution fires again and now finds the new profile.
    async with runtime.scope_provider() as _db:
        pass

    assert runtime.profile_id == new_profile_id, (
        "StaticRuntime must update profile_id on retry after a live profile appears "
        f"(expected {new_profile_id}, got {runtime.profile_id})"
    )


# ---------------------------------------------------------------------------
# Test: _pending_reinit recovery path after failed migration during swap
# ---------------------------------------------------------------------------


async def test_pending_reinit_recovery_after_failed_migration(
    tmp_path: Path,
) -> None:
    """A failed swap reinit leaves _pending_reinit set; the next scope entry retries and recovers.

    Arrange: DB A (marker profile 'pend-a'), DB B (marker 'pend-b').
    Act:
      1. Init A; swap B over A.
      2. Patch _apply_migrations_sync to raise RuntimeError.
      3. Enter session_scope(): staleness check disposes old engine and attempts
         reinit, which fails — exception propagates out of session_scope().
      4. Verify _db_path is None and _pending_reinit is not None.
      5. Remove patch; enter session_scope() again — recovery reinits successfully,
         B's profile is visible, and _pending_reinit is cleared.
    """
    path_a = tmp_path / "pend_a.db"
    path_b = tmp_path / "pend_b.db"

    await _build_db_with_profile(path_a, "pend_a@example.com", "pend-a")
    await _build_db_with_profile(path_b, "pend_b@example.com", "pend-b")

    await init_database(str(path_a))
    os.replace(path_b, path_a)

    with unittest.mock.patch(
        "snore.database.session._apply_migrations_sync",
        side_effect=RuntimeError("simulated migration failure"),
    ):
        with pytest.raises(RuntimeError):
            async with session_scope() as _db:
                pass  # pragma: no cover

    assert sess_mod._db_path is None, "After failed reinit, _db_path must be None"
    assert sess_mod._pending_reinit is not None, (
        "After failed reinit, _pending_reinit must be set so recovery can retry"
    )

    # Recovery: patch removed; next scope entry reinitializes successfully.
    async with session_scope() as db:
        profiles = (await db.execute(select(Profile))).scalars().all()
    names = {p.name for p in profiles}

    assert "pend-b" in names, "After recovery, B's profile must be visible"
    assert sess_mod._pending_reinit is None, (
        "_pending_reinit must be cleared after successful recovery"
    )


# ---------------------------------------------------------------------------
# Test: get_raw_session staleness coverage via FastAPI dependency
# ---------------------------------------------------------------------------


async def test_get_raw_session_detects_staleness_after_swap(tmp_path: Path) -> None:
    """get_raw_session drives the FastAPI dependency which triggers staleness detection."""
    from snore.api.deps import get_raw_session

    path_a = tmp_path / "deps_a.db"
    path_b = tmp_path / "deps_b.db"

    await _build_db_with_profile(path_a, "deps_a@example.com", "deps-a")
    await _build_db_with_profile(path_b, "deps_b@example.com", "deps-b")

    await init_database(str(path_a))
    engine_before = sess_mod._engine
    os.replace(path_b, path_a)

    agen = get_raw_session()
    session = await anext(agen)
    try:
        profiles = (await session.execute(select(Profile))).scalars().all()
    finally:
        await agen.aclose()

    names = {p.name for p in profiles}
    assert "deps-b" in names, (
        "get_raw_session must serve B's data after staleness detection"
    )
    assert sess_mod._engine is not engine_before, (
        "Engine must be replaced after staleness detection via get_raw_session"
    )


# ---------------------------------------------------------------------------
# Test: non-file targets skip staleness detection (backend-agnosticism)
# ---------------------------------------------------------------------------


async def test_memory_db_skips_staleness_detection(tmp_path: Path) -> None:
    """Detection is file-backed-SQLite-only so the storage layer stays dialect-agnostic.

    A future PostgreSQL target takes the same no-op path via _db_path is None.
    """
    await init_database(":memory:")

    assert sess_mod._db_identity is None, (
        "In-memory DB must not set _db_identity (no file inode to track)"
    )
    engine_before = sess_mod._engine

    async with session_scope() as db:
        await db.execute(text("SELECT 1"))

    assert sess_mod._engine is engine_before, (
        "Engine must not change for in-memory DB (no inode to detect)"
    )

    await cleanup_database()
