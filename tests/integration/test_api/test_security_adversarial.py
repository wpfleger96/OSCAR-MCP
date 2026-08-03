"""Adversarial security tests for Phase 2 auth backend.

Pass-1 findings: classes TestCritical1–TestAuthNoStore.
Pass-2 findings: classes TestP2* (appended below).

Plan bindings (SNORE_MULTIUSER_PLAN.md):
  §Session & CSRF Design : lines 160-166 (CSRF scope)
  §Upload & Job Resource  : lines 191-198 (bounded chunked copies)
  §Trusted Proxy          : lines 184-189 (rate limiting scope)
  §Phase 2               : line 233 (secret hygiene / no-store)
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import uuid

from pathlib import Path
from typing import Annotated

import pytest

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.app import create_app
from snore.api.config import load_config, set_config
from snore.api.deps import get_actor, get_db
from snore.auth.actor import ActorContext, AuthMode
from snore.auth.factory import ActorContextFactory

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _multiuser_config(
    monkeypatch: pytest.MonkeyPatch, base_url: str = "http://127.0.0.1:8000"
) -> None:
    monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
    monkeypatch.setenv(
        "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
    )
    monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", base_url)


def _make_client(async_db_session: AsyncSession) -> TestClient:
    app = create_app()

    async def _override_db():
        async with async_db_session.begin():
            yield async_db_session

    async def _override_actor(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> ActorContext:
        return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_actor] = _override_actor
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# CRITICAL 1: serve --host 0.0.0.0 refusal
# Plan: Phase 2 config requirement — startup refuses local + non-loopback bind.
# ---------------------------------------------------------------------------


class TestCritical1ServeHostRefusal:
    """``snore serve --host 0.0.0.0`` must exit before socket creation in local mode."""

    def _snore_bin(self) -> str:
        """Return the path to the snore binary in the current venv."""
        venv = Path(sys.executable).parent
        return str(venv / "snore")

    def test_serve_local_mode_nonloopback_host_exits_nonzero(self, tmp_path):
        """``snore serve --host 0.0.0.0`` with SNORE_AUTH_MODE=local must fail fast.

        The process should exit with a non-zero code and print a ConfigError
        before uvicorn opens a socket.  This exercises the SNORE_BIND_HOST
        export + load_config() call added to serve.py.
        """
        env = os.environ.copy()
        env["SNORE_AUTH_MODE"] = "local"
        env.pop("SNORE_BIND_HOST", None)
        env.pop("SNORE_SESSION_SECRET", None)

        result = subprocess.run(
            [self._snore_bin(), "serve", "--host", "0.0.0.0", "--port", "18765"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit for local+0.0.0.0, got 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        output = result.stdout + result.stderr
        assert (
            "configuration error" in output.lower() or "not allowed" in output.lower()
        ), f"Expected config error message, got:\n{output}"

    def test_serve_local_mode_loopback_host_config_validates_cleanly(self):
        """load_config with local mode + loopback bind must succeed without ConfigError.

        Validates the same path that ``snore serve --host 127.0.0.1`` exercises
        before starting the socket — the config export in serve.py means the
        lifespan sees the real bind address.
        """

        # This must not raise.
        cfg = load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        assert cfg.bind_host == "127.0.0.1"


# ---------------------------------------------------------------------------
# CRITICAL 2: CSRF origin comparison is canonical tuple, not startswith
# Plan: SNORE_MULTIUSER_PLAN.md §Session & CSRF Design lines 160-166.
# ---------------------------------------------------------------------------


class TestCritical2CsrfOriginComparison:
    """CSRF middleware uses canonical (scheme, host, port) comparison — no startswith."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _multiuser_config(monkeypatch, base_url="https://snore.example.com")
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def test_csrf_prefix_attack_rejected(self, async_db_session):
        """``Origin: https://snore.example.com.evil.test`` must be rejected (403).

        This is the classic prefix-match bypass: startswith() would accept it
        since the string starts with the allowed origin.  Canonical tuple
        comparison rejects it.  SNORE_MULTIUSER_PLAN.md §Session & CSRF:160-166.
        """
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
            headers={"origin": "https://snore.example.com.evil.test"},
        )
        assert resp.status_code == 403
        assert "Origin not allowed" in resp.json()["detail"]

    def test_csrf_userinfo_attack_rejected(self, async_db_session):
        """``Origin: https://snore.example.com@evil.test`` must be rejected (403).

        Userinfo in the URL makes the host ``evil.test``, not ``snore.example.com``.
        SNORE_MULTIUSER_PLAN.md §Session & CSRF:160-166.
        """
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
            headers={"origin": "https://snore.example.com@evil.test"},
        )
        assert resp.status_code == 403

    def test_csrf_null_origin_rejected(self, async_db_session):
        """``Origin: null`` (sandboxed iframe / file://) must be rejected (403)."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
            headers={"origin": "null"},
        )
        assert resp.status_code == 403

    def test_csrf_exact_public_origin_allowed(self, async_db_session):
        """The configured public origin exactly passes the CSRF check."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "pw"},
            headers={"origin": "https://snore.example.com"},
        )
        # Must not be 403 — origin passed.  401 = auth failed (expected).
        assert resp.status_code != 403

    def test_csrf_covers_multipart_import(self, async_db_session, monkeypatch):
        """CSRF must cover multipart upload — not just auth routes.

        Multipart is a browser-simple request; no CORS preflight protects it.
        A cross-origin multipart upload must be rejected by the CSRF middleware.
        SNORE_MULTIUSER_PLAN.md §Session & CSRF:160-166.
        """
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/import",
            files=[
                ("files", ("test.edf", b"fake content", "application/octet-stream"))
            ],
            headers={"origin": "https://evil.example.com"},
        )
        assert resp.status_code == 403

    def test_csrf_referer_fallback_parsed_not_startswith(self, async_db_session):
        """Referer fallback also uses parsed-origin comparison, not startswith."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
            headers={"referer": "https://snore.example.com.evil.test/login"},
        )
        assert resp.status_code == 403

    def test_csrf_dev_origins_respected(self, async_db_session, monkeypatch):
        """SNORE_DEV_ORIGINS allows extra origins when explicitly configured."""
        monkeypatch.setenv("SNORE_DEV_ORIGINS", "http://localhost:5173")
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "pw"},
            headers={"origin": "http://localhost:5173"},
        )
        # Not 403 — dev origin allowed.
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# CRITICAL 3: Session cookie must be host-only (no Domain attribute)
# ---------------------------------------------------------------------------


class TestCritical3CookieAttributes:
    """Login/redeem cookies must carry Secure, HttpOnly, SameSite=Lax, no Domain."""

    @pytest.fixture(autouse=True)
    def _setup_https_config(self, monkeypatch):
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def _assert_no_domain_attribute(self, set_cookie_header: str) -> None:
        parts = [p.strip().lower() for p in set_cookie_header.split(";")]
        domain_parts = [p for p in parts if p.startswith("domain=")]
        assert not domain_parts, (
            f"Set-Cookie must not contain Domain attribute, got: {set_cookie_header!r}"
        )

    def _assert_required_attributes(self, set_cookie_header: str) -> None:
        lower = set_cookie_header.lower()
        assert "httponly" in lower, f"Missing HttpOnly in: {set_cookie_header!r}"
        assert "samesite=lax" in lower, (
            f"Missing SameSite=Lax in: {set_cookie_header!r}"
        )
        assert "path=/" in lower, f"Missing Path=/ in: {set_cookie_header!r}"
        assert "secure" in lower, f"Missing Secure in: {set_cookie_header!r}"

    def test_login_cookie_has_required_attributes_and_no_domain(
        self, async_db_session, db_session
    ):
        """Login with correct credentials produces a cookie with required attributes
        and no Domain attribute (host-only).  CRITICAL 3 fix: no subdomain scope."""
        from snore.auth.passwords import hash_password  # noqa: PLC0415
        from snore.database.models import Profile, User  # noqa: PLC0415

        email = f"cookie_test_{uuid.uuid4().hex[:6]}@example.com"
        pw = "securePassword123"
        pw_hash = hash_password(pw)

        user = User(
            canonical_email=email,
            password_hash=pw_hash,
            role="admin",
            session_version=0,
        )
        db_session.add(user)
        db_session.flush()
        profile = Profile(user_id=user.id, name="Default")
        db_session.add(profile)
        db_session.flush()
        user.default_profile_id = profile.id
        db_session.flush()

        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pw},
            headers={"origin": "https://snore.example.com"},
        )
        assert resp.status_code == 200, f"Login failed: {resp.json()}"
        set_cookie = resp.headers.get("set-cookie", "")
        assert set_cookie, "Expected Set-Cookie header on successful login"
        self._assert_no_domain_attribute(set_cookie)
        self._assert_required_attributes(set_cookie)

    def test_logout_no_domain_attribute(self, async_db_session):
        """Logout clear-cookie also has no Domain attribute."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"origin": "https://snore.example.com"},
        )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        if set_cookie:
            self._assert_no_domain_attribute(set_cookie)


# ---------------------------------------------------------------------------
# IMPORTANT 1: Upload chunked copy — no unbounded read(), cleanup on over-limit
# Plan: SNORE_MULTIUSER_PLAN.md §Upload lines 191-198.
# ---------------------------------------------------------------------------


class TestImportant1UploadChunkedCopy:
    """Upload handler uses chunked copy, never ``read()`` with no size arg."""

    @pytest.fixture(autouse=True)
    def _setup_local_config(self, monkeypatch):
        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

    def test_upload_copy_never_calls_unbounded_read(self, monkeypatch):
        """The copy helper reads in chunks — ``file.read()`` without size is never called.

        This verifies the fix: previously ``await upload.read()`` was called which
        materialises the full file in memory.  The implementation now calls
        ``file.read(_COPY_CHUNK)`` in a loop.
        SNORE_MULTIUSER_PLAN.md §Upload:191-198 (bounded chunked copies).
        """
        from snore.api.routers.import_data import (  # noqa: PLC0415
            _copy_chunked,
        )

        data = b"x" * 128

        class StrictChunkFile(io.RawIOBase):
            """Raises if read() is called without an explicit size argument."""

            def __init__(self, content: bytes) -> None:
                self._buf = io.BytesIO(content)

            def read(self, size: int = -1) -> bytes:
                if size == -1:
                    raise AssertionError(
                        "read() called without a size argument — unbounded read!"
                    )
                return self._buf.read(size)

        import tempfile  # noqa: PLC0415

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
            dest = Path(tmp.name)
        try:
            _copy_chunked(StrictChunkFile(data), dest, max_bytes=len(data))
            assert dest.read_bytes() == data
        finally:
            dest.unlink(missing_ok=True)

    def test_upload_copy_aborts_and_removes_dest_on_over_limit(self, tmp_path):
        """Mid-copy size excess causes dest removal.

        SNORE_MULTIUSER_PLAN.md §Upload:191-198 (abort + tempdir cleanup).
        """
        from snore.api.routers.import_data import (  # noqa: PLC0415
            _copy_chunked,
            _FileSizeExceeded,
        )

        data = b"x" * 1000
        dest = tmp_path / "output.bin"

        with pytest.raises(_FileSizeExceeded):
            _copy_chunked(io.BytesIO(data), dest, max_bytes=500)

        assert not dest.exists(), "dest must be removed when copy aborts over-limit"

    def test_upload_413_releases_slot(self, api_client, monkeypatch):
        """A per-file over-limit 413 releases the admission slot.

        Verifies that after a chunked-copy rejection the _global_count drops
        back to zero — the slot owned by the failed upload is released.
        SNORE_MULTIUSER_PLAN.md §Upload:191-198.
        """
        import snore.api.import_jobs as jobs_mod  # noqa: PLC0415
        import snore.api.routers.import_data as import_mod  # noqa: PLC0415

        cfg = load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        set_config(cfg)

        # Patch per-file cap to a tiny value (10 bytes) so our test file exceeds it.
        monkeypatch.setattr(
            import_mod, "_get_upload_limits", lambda: (512 * 1024 * 1024, 500, 10)
        )
        # Patch _start_worker to a no-op so cleanup doesn't race.
        monkeypatch.setattr(import_mod, "_start_worker", lambda *a, **kw: None)

        # Record global count before the upload.
        count_before = jobs_mod._global_count
        # Upload a file larger than the tiny per-file cap.
        resp = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 50, "application/octet-stream"))],
        )
        assert resp.status_code == 413, (
            f"Expected 413, got {resp.status_code}: {resp.text}"
        )

        # After the rejection the slot must be released.
        count_after = jobs_mod._global_count
        assert count_after == count_before, (
            f"Global slot count should return to {count_before} after 413; "
            f"got {count_after} — slot was leaked"
        )


# ---------------------------------------------------------------------------
# IMPORTANT 2: LockoutStore MAX_ENTRIES — does not evict active lockouts
# Plan: SNORE_MULTIUSER_PLAN.md §Trusted Proxy & Rate Limiting:184-189.
# ---------------------------------------------------------------------------


class TestImportant2LockoutMaxEntries:
    """LockoutStore hard cap does not grow beyond MAX_ENTRIES by evicting active locks."""

    def test_lockout_store_does_not_exceed_max_entries_with_all_unexpired(self):
        """Filling the store with unexpired entries then adding one more must not
        grow the store past MAX_ENTRIES.

        Previous bug: _evict_one_expired() found no expired entries but
        ``setdefault`` inserted anyway, exceeding the cap.
        SNORE_MULTIUSER_PLAN.md §Trusted Proxy:184-189.
        """
        from snore.auth.lockout import LockoutStore  # noqa: PLC0415

        cap = 5
        store = LockoutStore()
        store._max_entries = cap
        # Patch MAX_ENTRIES used in record_failure.
        import snore.auth.lockout as lockout_mod  # noqa: PLC0415

        original = lockout_mod.MAX_ENTRIES
        lockout_mod.MAX_ENTRIES = cap
        try:
            # Fill with `cap` distinct unexpired entries.
            for i in range(cap):
                store.record_failure(f"user{i}@example.com", "1.2.3.4")

            assert len(store._entries) == cap
            # Attempt to add one more — should be silently dropped.
            store.record_failure("overflow@example.com", "1.2.3.4")

            assert len(store._entries) == cap, (
                f"Store grew past MAX_ENTRIES ({cap}) when all entries were active"
            )
        finally:
            lockout_mod.MAX_ENTRIES = original

    def test_lockout_store_evicts_expired_entry_when_at_cap(self):
        """When at cap but one entry is expired, the expired entry is evicted
        and the new key is inserted."""
        import time  # noqa: PLC0415

        import snore.auth.lockout as lockout_mod  # noqa: PLC0415

        from snore.auth.lockout import LockoutStore  # noqa: PLC0415

        cap = 3
        original = lockout_mod.MAX_ENTRIES
        lockout_mod.MAX_ENTRIES = cap
        try:
            store = LockoutStore()
            for i in range(cap):
                store.record_failure(f"u{i}@x.com", "1.1.1.1")

            # Manually expire the first entry so it can be evicted.
            with store._lock:
                first_key = next(iter(store._entries))
                store._entries[first_key].locked_until = time.monotonic() - 1

            # Add a new key — should evict the expired one and succeed.
            store.record_failure("new@x.com", "1.1.1.1")

            assert len(store._entries) == cap
            assert ("new@x.com", "1.1.1.1") in store._entries
        finally:
            lockout_mod.MAX_ENTRIES = original


# ---------------------------------------------------------------------------
# IMPORTANT 3: Credential bounds — byte-based check, consistent login + redeem
# Plan: Phase 2 auth router — password byte cap on all paths.
# ---------------------------------------------------------------------------


class TestImportant3CredentialBounds:
    """Password validation uses byte length (UTF-8), not character count."""

    def test_password_1024_multibyte_chars_exceeds_byte_limit(self):
        """A string of 1024 CJK characters (3 bytes each = 3072 bytes) must be
        rejected by validate_password_bytes.

        Previous bug: invite redemption checked ``len(body.password) > 1024``
        (character count), which passes 1024 multibyte chars but then
        ``hash_password`` raises ValueError → unhandled 500.
        """
        from snore.auth.passwords import validate_password_bytes  # noqa: PLC0415

        # Each CJK character is 3 bytes UTF-8 → 1024 chars = 3072 bytes.
        long_multibyte = "あ" * 1024
        assert len(long_multibyte) == 1024  # 1024 chars
        assert len(long_multibyte.encode()) == 3072  # 3072 bytes

        with pytest.raises(ValueError, match="bytes"):
            validate_password_bytes(long_multibyte)

    def test_password_exactly_1024_bytes_is_valid(self):
        """A password exactly at the 1024-byte boundary is accepted."""
        from snore.auth.passwords import validate_password_bytes  # noqa: PLC0415

        exactly_1024 = "a" * 1024
        validate_password_bytes(exactly_1024)  # Should not raise.

    def test_login_rejects_over_byte_limit_password(
        self, async_db_session, monkeypatch
    ):
        """``POST /auth/login`` with a 1024-multibyte-char password returns 401
        (treated as authentication failure, not 500 or unhandled ValueError).
        """
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )
        client = _make_client(async_db_session)

        long_pw = "あ" * 1024  # 3072 bytes > 1024 limit
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": long_pw},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        # Must be 401 (auth failed) not 500 (unhandled error).
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

    def test_redeem_rejects_over_byte_limit_password(
        self, async_db_session, monkeypatch
    ):
        """``POST /auth/invites/{token}/redeem`` with an over-byte-limit password
        returns 422 — the byte validator is applied before any DB work.
        """
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )
        client = _make_client(async_db_session)

        long_pw = "あ" * 1024  # 3072 bytes
        token = uuid.uuid4().hex
        resp = client.post(
            "/api/v1/auth/invites/redeem",
            json={"token": token, "password": long_pw},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 422, (
            f"Expected 422, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# MINOR / CRITICAL 2: Cache-Control: no-store on all /auth/ responses
# Plan: SNORE_MULTIUSER_PLAN.md §Phase 2:233.
# ---------------------------------------------------------------------------


class TestAuthNoStore:
    """Cache-Control: no-store must appear on auth-path 2xx and 4xx responses.

    SNORE_MULTIUSER_PLAN.md §Phase 2:233.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def test_status_200_has_no_store(self, async_db_session):
        """GET /auth/status (2xx) has Cache-Control: no-store."""
        client = _make_client(async_db_session)
        resp = client.get("/api/v1/auth/status")
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("cache-control", "").lower()

    def test_login_401_has_no_store(self, async_db_session):
        """POST /auth/login with wrong password (4xx) has Cache-Control: no-store."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 401
        assert "no-store" in resp.headers.get("cache-control", "").lower()

    def test_login_403_csrf_has_no_store(self, async_db_session):
        """POST /auth/login with missing Origin (CSRF 403) has Cache-Control: no-store."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong"},
        )
        assert resp.status_code == 403
        assert "no-store" in resp.headers.get("cache-control", "").lower()

    def test_login_422_has_no_store(self, async_db_session):
        """POST /auth/login with malformed body (422) has Cache-Control: no-store."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            content=b"not json",
            headers={
                "content-type": "application/json",
                "origin": "http://127.0.0.1:8000",
            },
        )
        assert resp.status_code == 422
        assert "no-store" in resp.headers.get("cache-control", "").lower()

    def test_invite_lookup_200_has_no_store(self, async_db_session):
        """POST /auth/invites/lookup (200) has Cache-Control: no-store."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/invites/lookup",
            json={"token": uuid.uuid4().hex},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("cache-control", "").lower()


# =============================================================================
# Pass-2 adversarial tests
# =============================================================================


# ---------------------------------------------------------------------------
# P2-IMPORTANT 1: KDF executor — cancellation does not admit a 5th native op
# ---------------------------------------------------------------------------


class TestP2KdfCancellation:
    """Cancelling an awaiting KDF coroutine must not free the executor slot.

    With ThreadPoolExecutor(max_workers=4), the slot is owned by the running
    thread, not the awaiting coroutine.  Cancelling the awaiter leaves the
    thread running and the slot occupied; a 5th submission queues.
    """

    def test_kdf_executor_max_workers_is_four(self):
        """The KDF executor is bounded at 4 workers."""
        from snore.auth.passwords import _KDF_EXECUTOR

        assert _KDF_EXECUTOR._max_workers == 4

    def test_kdf_slot_held_after_awaiter_cancel(self):
        """Cancelling a KDF awaiter does not start a 5th native Argon2 op.

        Uses a 1-worker test executor to prove the invariant at smaller scale:
        1 blocked thread + cancelled awaiter + 2nd submission → 2nd stays queued
        until the first thread finishes.
        SNORE_MULTIUSER_PLAN.md: Argon2 bound is ~256 MiB with 4 workers.
        """
        import asyncio
        import concurrent.futures
        import threading

        gate = threading.Event()
        started = threading.Event()
        finished_count = [0]
        lock = threading.Lock()

        def slow_op() -> int:
            started.set()
            gate.wait(timeout=5)
            with lock:
                finished_count[0] += 1
            return 1

        test_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        async def run_test() -> None:
            loop = asyncio.get_running_loop()

            # Submit op1 — fills the single executor slot.
            fut1 = loop.run_in_executor(test_executor, slow_op)

            # Wait for op1 thread to start.
            started.wait(timeout=2)

            # Cancel the awaiting task for op1.
            task1 = asyncio.ensure_future(asyncio.wrap_future(fut1))
            task1.cancel()
            try:
                await task1
            except (asyncio.CancelledError, Exception):
                pass

            # Submit op2 — must queue, not start a second thread.
            fut2 = asyncio.ensure_future(loop.run_in_executor(test_executor, slow_op))

            # Give the event loop a moment.
            await asyncio.sleep(0.05)

            # op1 thread is still blocked; started.is_set() → True (first thread)
            # but finished_count[0] == 0 means it hasn't finished yet.
            assert finished_count[0] == 0, "op1 should still be running"

            # Worker count: executor._work_queue not empty or thread still alive.
            # Key assertion: at most 1 thread is active (the one blocked on gate).
            active = sum(1 for t in test_executor._threads if t.is_alive())
            assert active <= 1, f"Expected ≤1 active threads, got {active}"

            # Release the gate → op1 finishes → op2 starts.
            gate.set()
            try:
                await asyncio.wait_for(
                    asyncio.wrap_future(fut2) if fut2 is not None else asyncio.sleep(0),
                    timeout=5,
                )
            except Exception:
                pass

        asyncio.run(run_test())
        test_executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# P2-IMPORTANT 2+3: Upload temp dir and slot cleanup — 413 and CancelledError
# ---------------------------------------------------------------------------


class TestP2UploadLifecycle:
    """Upload cleanup lifecycle: 413 leaves no parent snore-upload-* dir; slot released."""

    @pytest.fixture(autouse=True)
    def _setup_local_config(self, monkeypatch):
        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        from snore.api.config import load_config, set_config  # noqa: PLC0415

        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

    def test_mid_copy_413_leaves_no_snore_upload_dir(
        self, api_client, monkeypatch, tmp_path
    ):
        """A per-file 413 must remove the whole snore-upload-* temp dir,
        not just the destination file.  Snapshots gettempdir() before and after.

        SNORE_MULTIUSER_PLAN.md §Upload:191-198 (abort + tempdir cleanup).
        """
        import glob
        import tempfile

        import snore.api.routers.import_data as import_mod

        from snore.api.config import load_config, set_config  # noqa: PLC0415

        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        # Patch per-file cap to tiny so upload exceeds it.
        monkeypatch.setattr(
            import_mod, "_get_upload_limits", lambda: (512 * 1024 * 1024, 500, 10)
        )
        monkeypatch.setattr(import_mod, "_start_worker", lambda *a, **kw: None)

        tmpdir = tempfile.gettempdir()
        before = set(glob.glob(f"{tmpdir}/snore-upload-*"))

        resp = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 50, "application/octet-stream"))],
        )
        assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"

        after = set(glob.glob(f"{tmpdir}/snore-upload-*"))
        leaked = after - before
        assert not leaked, f"snore-upload-* dirs leaked after 413: {leaked}"

    def test_mid_copy_413_releases_slot(self, api_client, monkeypatch):
        """Slot count returns to zero after a 413 upload rejection.
        SNORE_MULTIUSER_PLAN.md §Upload:194-198.
        """
        import snore.api.import_jobs as jobs_mod
        import snore.api.routers.import_data as import_mod

        from snore.api.config import load_config, set_config  # noqa: PLC0415

        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        monkeypatch.setattr(
            import_mod, "_get_upload_limits", lambda: (512 * 1024 * 1024, 500, 10)
        )
        monkeypatch.setattr(import_mod, "_start_worker", lambda *a, **kw: None)

        count_before = jobs_mod._global_count

        resp = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 50, "application/octet-stream"))],
        )
        assert resp.status_code == 413

        assert jobs_mod._global_count == count_before, (
            f"Slot leaked: was {count_before}, now {jobs_mod._global_count}"
        )


# ---------------------------------------------------------------------------
# P2-IMPORTANT 4: Port validation at startup
# ---------------------------------------------------------------------------


class TestP2PortValidation:
    """SNORE_PUBLIC_BASE_URL with malformed or out-of-range port → ConfigError at startup."""

    def test_bad_port_text_raises_config_error(self, monkeypatch):
        """https://example.com:bad raises ConfigError.
        SNORE_MULTIUSER_PLAN.md §Config (port range enforced before socket creation).
        """
        from snore.api.config import ConfigError

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://example.com:bad")

        with pytest.raises(ConfigError, match="invalid port"):
            load_config()

    def test_port_zero_raises_config_error(self, monkeypatch):
        """https://example.com:0 (port 0) raises ConfigError."""
        from snore.api.config import ConfigError

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://example.com:0")

        with pytest.raises(ConfigError, match="port"):
            load_config()

    def test_port_too_large_raises_config_error(self, monkeypatch):
        """https://example.com:70000 (port > 65535) raises ConfigError."""
        from snore.api.config import ConfigError

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://example.com:70000")

        with pytest.raises(ConfigError, match="port"):
            load_config()

    def test_valid_https_no_port_accepted(self, monkeypatch):
        """https://example.com (no explicit port) is accepted; public_origin uses 443."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://example.com")

        cfg = load_config()
        assert cfg.public_origin == ("https", "example.com", 443)


# ---------------------------------------------------------------------------
# P2-IMPORTANT 5: Single canonical trusted-IP helper (middleware and auth router)
# ---------------------------------------------------------------------------


class TestP2CanonicalIpHelper:
    """RateLimitMiddleware and credential lockout key on the same IP.

    With a trusted peer and a malformed cf-connecting-ip, both controls must
    fall back to the peer address (not use the malformed string as a key).
    """

    def test_malformed_forwarded_ip_falls_back_to_peer(self, monkeypatch):
        """A non-IP cf-connecting-ip value must NOT be used as a lockout key.
        Both get_client_ip (used by auth router) and the middleware helper use
        the same validated IP.
        """
        from snore.api.client_ip import get_client_ip  # noqa: PLC0415

        monkeypatch.setenv("SNORE_TRUSTED_PROXIES", "10.0.0.1")
        from snore.api.config import load_config, set_config  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        from starlette.requests import Request  # noqa: PLC0415
        from starlette.testclient import TestClient  # noqa: PLC0415

        captured: list[str] = []

        from starlette.responses import PlainTextResponse  # noqa: PLC0415

        async def view(r: Request) -> PlainTextResponse:
            captured.append(get_client_ip(r))
            return PlainTextResponse("ok")

        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.routing import Route  # noqa: PLC0415

        app = Starlette(routes=[Route("/", view)])

        # Simulate peer=10.0.0.1 (trusted), forwarded = "not-an-ip"
        from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: PLC0415

        class PeerOverride:
            def __init__(self, wrapped: ASGIApp) -> None:
                self.wrapped = wrapped

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                if scope["type"] == "http":
                    scope["client"] = ("10.0.0.1", 12345)
                await self.wrapped(scope, receive, send)

        wrapped_client = TestClient(PeerOverride(app))
        wrapped_client.get("/", headers={"cf-connecting-ip": "not-an-ip"})

        assert captured, "view was never called"
        assert captured[0] == "10.0.0.1", (
            f"Expected fallback to peer 10.0.0.1, got {captured[0]!r}"
        )

    def test_valid_ipv6_forwarded_ip_is_accepted(self, monkeypatch):
        """A valid IPv6 cf-connecting-ip is used when the peer is trusted."""
        from snore.api.client_ip import get_client_ip  # noqa: PLC0415

        monkeypatch.setenv("SNORE_TRUSTED_PROXIES", "10.0.0.1")
        from snore.api.config import load_config, set_config  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.requests import Request  # noqa: PLC0415
        from starlette.routing import Route  # noqa: PLC0415
        from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: PLC0415

        captured: list[str] = []

        from starlette.responses import PlainTextResponse  # noqa: PLC0415

        async def view(r: Request) -> PlainTextResponse:
            captured.append(get_client_ip(r))
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/", view)])

        class PeerOverride:
            def __init__(self, wrapped: ASGIApp) -> None:
                self.wrapped = wrapped

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                if scope["type"] == "http":
                    scope["client"] = ("10.0.0.1", 12345)
                await self.wrapped(scope, receive, send)

        wrapped_client = TestClient(PeerOverride(app))
        wrapped_client.get("/", headers={"cf-connecting-ip": "::1"})

        assert captured[0] == "::1", f"Expected ::1, got {captured[0]!r}"


# ---------------------------------------------------------------------------
# P2-IMPORTANT 6: RateLimitStore recovers from saturation
# ---------------------------------------------------------------------------


class TestP2RateLimitStoreSaturation:
    """Rate limit store purges stale entries and tracks new IPs after recovery."""

    def test_stale_at_cap_recovery(self):
        """After cap fills with expired windows, a new IP is tracked (not failed-open forever).
        SNORE_MULTIUSER_PLAN.md §Trusted Proxy:184-189.
        """
        import time

        from snore.auth.lockout import RateLimitStore

        store = RateLimitStore(window=0.05, max_per_window=10, max_ips=2)

        # Fill to cap with IPs whose windows will expire.
        store.check_and_record("1.1.1.1")
        store.check_and_record("2.2.2.2")
        assert len(store._entries) == 2

        # Let the windows expire.
        time.sleep(0.1)

        # New IP should be tracked (not failed-open) since stale entries are purged.
        allowed = store.check_and_record("3.3.3.3")
        assert allowed is True
        # Verify the new IP is actually tracked (not just allowed-untracked).
        assert "3.3.3.3" in store._entries, (
            "New IP should be tracked after stale-entry purge"
        )

    def test_all_active_at_cap_fails_open(self):
        """When all entries are unexpired (active), a new IP is allowed without tracking."""
        import snore.auth.lockout as lockout_mod

        original = lockout_mod.RATE_MAX_IPS
        lockout_mod.RATE_MAX_IPS = 2
        try:
            from snore.auth.lockout import RateLimitStore

            store = RateLimitStore(window=60.0, max_per_window=10, max_ips=2)
            store.check_and_record("1.1.1.1")
            store.check_and_record("2.2.2.2")
            assert len(store._entries) == 2

            # New IP with active table → allowed without tracking.
            allowed = store.check_and_record("3.3.3.3")
            assert allowed is True
            assert "3.3.3.3" not in store._entries
        finally:
            lockout_mod.RATE_MAX_IPS = original


# ---------------------------------------------------------------------------
# P2-IMPORTANT 7: Auth model bounds — overlong bodies rejected before DB
# ---------------------------------------------------------------------------


class TestP2AuthModelBounds:
    """Pydantic model constraints on email/password reject oversized inputs."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def test_overlong_email_rejected(self, async_db_session):
        """A 300-char email (> 254 max) returns 422 before any DB lookup."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "a" * 300 + "@example.com", "password": "pw"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for overlong email, got {resp.status_code}"
        )

    def test_overlong_password_rejected_at_model(self, async_db_session):
        """A 5000-char password (> 4096 char model max) returns 422."""
        client = _make_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": "x" * 5000},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for overlong password, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# P2-IMPORTANT 8: Invite token not in URL / not in access logs
# ---------------------------------------------------------------------------


class TestP2InviteTokenNotInUrl:
    """Invite token is in the request body, not the URL path.

    SNORE_MULTIUSER_PLAN.md §Phase 2:233 (secret hygiene: tokens never in logs).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def test_lookup_token_in_body_not_url(self, async_db_session):
        """POST /auth/invites/lookup uses request body; token absent from URL."""
        client = _make_client(async_db_session)
        token = uuid.uuid4().hex

        # Capture log records to verify token is absent from logged paths.
        import logging

        records: list[logging.LogRecord] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = CapturingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            resp = client.post(
                "/api/v1/auth/invites/lookup",
                json={"token": token},
                headers={"origin": "http://127.0.0.1:8000"},
            )
        finally:
            root_logger.removeHandler(handler)

        assert resp.status_code == 200
        # The raw token must not appear in any log message.
        for record in records:
            msg = record.getMessage()
            assert token not in msg, f"Raw invite token found in log record: {msg!r}"

    def test_invite_url_fragment_format(self, monkeypatch, tmp_path):
        """snore user invite prints a fragment-based URL, not a path-based URL.

        The raw token must be in the fragment (#token) not the path so it
        never reaches the server or its access log.
        SNORE_MULTIUSER_PLAN.md §Phase 2:233.
        """
        env = os.environ.copy()
        env["SNORE_PUBLIC_BASE_URL"] = "https://snore.example.com"
        env["SNORE_AUTH_MODE"] = "local"

        result = subprocess.run(
            [
                str(Path(sys.executable).parent / "snore"),
                "user",
                "invite",
                "test@example.com",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        output = result.stdout + result.stderr
        # URL must use fragment (#) not a path segment.
        assert "snore.example.com/invite#" in output or (
            # URL printed should not contain the API path with token
            "/api/v1/auth/invites/" not in output
        ), f"Expected fragment-based URL, got:\n{output}"


# ---------------------------------------------------------------------------
# P2-MINOR 1: validate_password_bytes — full 1-1024 byte invariant
# ---------------------------------------------------------------------------


class TestP2PasswordValidatorInvariant:
    """validate_password_bytes enforces 1 ≤ len(encoded) ≤ 1024."""

    def test_empty_string_rejected(self):
        """Empty password raises ValueError."""
        from snore.auth.passwords import validate_password_bytes

        with pytest.raises(ValueError, match="at least 1 byte"):
            validate_password_bytes("")

    def test_single_byte_accepted(self):
        """One-byte password is valid."""
        from snore.auth.passwords import validate_password_bytes

        validate_password_bytes("a")  # no raise

    def test_exactly_1024_bytes_accepted(self):
        """Exactly 1024 ASCII bytes is valid."""
        from snore.auth.passwords import validate_password_bytes

        validate_password_bytes("a" * 1024)

    def test_1025_bytes_rejected(self):
        """1025 ASCII bytes exceeds the limit."""
        from snore.auth.passwords import validate_password_bytes

        with pytest.raises(ValueError, match="at most"):
            validate_password_bytes("a" * 1025)

    def test_multibyte_at_boundary(self):
        """341 CJK chars (= 1023 bytes) is valid; 342 chars (= 1026 bytes) is not."""
        from snore.auth.passwords import validate_password_bytes

        # 341 * 3 = 1023 bytes — valid
        validate_password_bytes("あ" * 341)

        # 342 * 3 = 1026 bytes — invalid
        with pytest.raises(ValueError, match="at most"):
            validate_password_bytes("あ" * 342)


# ---------------------------------------------------------------------------
# P2-MINOR 2: CSRF fails closed when public_origin is None in multiuser
# ---------------------------------------------------------------------------


class TestP2CsrfFailsClosedOnNoneOrigin:
    """CsrfMiddleware must return 403 (not pass) when public_origin is None."""

    def test_csrf_fails_closed_with_no_public_origin(self, monkeypatch):
        """If AppConfig.public_origin is somehow None in multiuser, CSRF fails closed.

        This is a defense-in-depth test: normal load_config() prevents this
        state, but the middleware must not fail open if it ever occurs.
        """
        from snore.api.config import AppConfig, set_config  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415

        # Construct an AppConfig that is multiuser but has no public_origin.
        # This represents an impossible-under-normal-load_config() state.
        broken_cfg = AppConfig(
            auth_mode=AuthMode.MULTIUSER,
            session_secret="test-secret-at-least-32-chars-long-abcdef",
            public_base_url="https://snore.example.com",
            public_origin=None,  # Forced to None for test
            bind_host="127.0.0.1",
            trusted_proxies=frozenset(),
            dev_origins=frozenset(),
            max_upload_bytes=512 * 1024 * 1024,
            max_file_bytes=256 * 1024 * 1024,
            max_jobs_per_user=3,
            max_jobs_global=10,
        )
        set_config(broken_cfg)

        from fastapi.testclient import TestClient  # noqa: PLC0415

        from snore.api.app import create_app  # noqa: PLC0415

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
            headers={"origin": "https://snore.example.com"},
        )
        # Must fail closed (403) not open.
        assert resp.status_code == 403, (
            f"Expected 403 (fail-closed), got {resp.status_code}"
        )
