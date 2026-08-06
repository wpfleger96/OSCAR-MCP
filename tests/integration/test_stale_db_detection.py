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
