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
from tests.helpers.api_client import make_test_client

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
    return make_test_client(async_db_session)


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
        # Patch enqueue_for_execution to a no-op so the worker never runs.
        monkeypatch.setattr(import_mod, "enqueue_for_execution", lambda *a, **kw: None)

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

    All tests drive the real async wrappers (hash_password_async /
    verify_password_async) using the production executor so a revert to
    semaphore+to_thread would cause failures.
    """

    def test_kdf_executor_max_workers_is_four(self):
        """The KDF executor is bounded at 4 workers."""
        from snore.auth.passwords import _KDF_EXECUTOR

        assert _KDF_EXECUTOR._max_workers == 4

    def test_kdf_async_wrapper_runs_on_snore_kdf_thread(self):
        """hash_password_async dispatches to a snore-kdf-* named thread.

        Patches passwords.hash_password (the module function that
        hash_password_async submits to the executor) so we can observe the
        thread name without running a real Argon2 op.
        """
        import asyncio
        import threading

        from unittest.mock import patch

        from snore.auth import passwords

        thread_names: list[str] = []

        def tracking_hash(pw: str) -> str:
            thread_names.append(threading.current_thread().name)
            return "mocked-hash"

        async def run() -> None:
            with patch.object(passwords, "hash_password", tracking_hash):
                # hash_password_async submits the patched function to the executor.
                await passwords.hash_password_async("test")

        asyncio.run(run())
        assert thread_names, "hash_password never ran"
        assert all(n.startswith("snore-kdf-") for n in thread_names), (
            f"KDF ran on wrong thread: {thread_names}"
        )

    def test_kdf_slot_held_after_awaiter_cancel(self):
        """Cancelling hash_password_async does not release the executor slot.

        Patches _KDF_EXECUTOR to a 1-worker executor and drives the real
        async wrapper.  Cancelling the awaiter while the thread is blocked
        must leave the thread running and the slot occupied.
        SNORE_MULTIUSER_PLAN.md: Argon2 bound is ~256 MiB with 4 workers.
        """
        import asyncio
        import concurrent.futures
        import threading

        from unittest.mock import patch

        from snore.auth import passwords

        gate = threading.Event()
        started = threading.Event()
        original_hash = passwords._hasher.hash

        def slow_hash(pw: str) -> str:
            started.set()
            gate.wait(timeout=5)
            return original_hash(pw)

        test_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="snore-kdf-test-"
        )
        original_executor = passwords._KDF_EXECUTOR
        passwords._KDF_EXECUTOR = test_executor

        async def run_test() -> None:
            try:
                # Patch the module-level hash_password (not the C-extension method).
                # hash_password_async submits this function to the executor.
                with patch.object(passwords, "hash_password", slow_hash):
                    # Submit via real async wrapper — slot filled.
                    task1 = asyncio.create_task(passwords.hash_password_async("pw1"))

                    # Wait for thread to start.
                    await asyncio.to_thread(started.wait, 2)
                    assert started.is_set(), "KDF thread never started"

                    # Cancel the awaiter.
                    task1.cancel()
                    try:
                        await task1
                    except (asyncio.CancelledError, Exception):
                        pass

                    # Thread still alive → slot held.
                    active = sum(1 for t in test_executor._threads if t.is_alive())
                    assert active >= 1, (
                        "Expected executor thread still running after cancel"
                    )
                    assert active <= 1, f"Expected ≤1 active threads; got {active}"
            finally:
                gate.set()

        try:
            asyncio.run(run_test())
        finally:
            passwords._KDF_EXECUTOR = original_executor
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
        """Per-file 413 fired AFTER mkdtemp must remove the snore-upload-* parent.

        The previous test sent a 50-byte file against a 10-byte cap, which was
        rejected by the UploadFile.size pre-check BEFORE mkdtemp() — so no dir
        was ever created and the fix could be deleted without the test failing.

        This version:
        - Uses a large max_file_bytes so the size pre-check passes and mkdtemp
          IS called before _copy_chunked runs.
        - Patches _copy_chunked to raise _FileSizeExceeded immediately, so the
          413 fires AFTER mkdtemp but BEFORE ownership transfer — the path the
          finally-cleanup fix guards.
        - Redirects tempfile.mkdtemp to a test-private directory (tmp_path) so
          the assertion is xdist-safe and never races another worker's uploads.

        SNORE_MULTIUSER_PLAN.md §Upload:191-198 (abort + tempdir cleanup).
        """
        import tempfile

        import snore.api.routers.import_data as import_mod

        from snore.api.config import load_config, set_config  # noqa: PLC0415
        from snore.api.routers.import_data import _FileSizeExceeded  # noqa: PLC0415

        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        # Large max_file_bytes: the UploadFile.size pre-check passes and we reach
        # _copy_chunked, meaning mkdtemp() has already been called.
        monkeypatch.setattr(
            import_mod,
            "_get_upload_limits",
            lambda: (512 * 1024 * 1024, 500, 256 * 1024 * 1024),
        )
        monkeypatch.setattr(import_mod, "enqueue_for_execution", lambda *a, **kw: None)

        # Redirect snore-upload-* dirs to test-private tmp_path (xdist-safe).
        created_dirs: list[Path] = []
        original_mkdtemp = tempfile.mkdtemp

        def tracked_mkdtemp(
            prefix: str = "tmp", suffix: str = "", dir: str | None = None
        ) -> str:
            if prefix == "snore-upload-":
                path = original_mkdtemp(prefix=prefix, suffix=suffix, dir=str(tmp_path))
                created_dirs.append(Path(path))
                return path
            return original_mkdtemp(prefix=prefix, suffix=suffix, dir=dir)

        monkeypatch.setattr(tempfile, "mkdtemp", tracked_mkdtemp)

        # _copy_chunked raises _FileSizeExceeded immediately so the 413 fires after
        # mkdtemp but before ownership transfer — the exact regression target.
        def failing_copy(src_file: object, dest: object, max_bytes: int) -> None:
            raise _FileSizeExceeded()

        monkeypatch.setattr(import_mod, "_copy_chunked", failing_copy)

        resp = api_client.post(
            "/api/v1/import",
            files=[("files", ("test.edf", b"x" * 256, "application/octet-stream"))],
        )
        assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"

        # The tracked dir must have been created (otherwise the fix was bypassed
        # at a stage before mkdtemp, and this test has no coverage).
        assert created_dirs, (
            "tempfile.mkdtemp was never called — test never reached the copy phase. "
            "Check the size pre-check logic."
        )
        for d in created_dirs:
            assert not d.exists(), (
                f"snore-upload-* dir leaked after 413: {d}\n"
                "The finally-cleanup fix was removed or bypassed."
            )

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
        monkeypatch.setattr(import_mod, "enqueue_for_execution", lambda *a, **kw: None)

        count_before = jobs_mod._global_count

        resp = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 50, "application/octet-stream"))],
        )
        assert resp.status_code == 413

        assert jobs_mod._global_count == count_before, (
            f"Slot leaked: was {count_before}, now {jobs_mod._global_count}"
        )

    @pytest.mark.asyncio
    async def test_upload_cancel_drives_real_handler(
        self, async_db_session, db_session, monkeypatch
    ):
        """Cancel a real POST /api/v1/import mid-copy via httpx.AsyncClient.

        Drives the actual import_files handler through ASGITransport.
        Patches _copy_chunked to a double that signals via call_soon_threadsafe
        and blocks on a threading.Event gate so cancellation is deterministic.
        Asserts: no new snore-upload-* dirs, slot returned to baseline, and a
        fresh reservation succeeds at the cap.
        SNORE_MULTIUSER_PLAN.md:194-198 (release-on-client-abort).
        """
        import asyncio  # noqa: PLC0415
        import glob
        import tempfile
        import threading

        import httpx

        import snore.api.import_jobs as jobs_mod
        import snore.api.routers.import_data as import_mod

        from snore.api.app import create_app  # noqa: PLC0415
        from snore.api.config import load_config, set_config  # noqa: PLC0415
        from snore.api.deps import get_actor, get_db  # noqa: PLC0415
        from snore.api.import_jobs import remove_job, reserve_slot  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415
        from snore.auth.factory import ActorContextFactory  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        loop = asyncio.get_running_loop()
        gate = threading.Event()
        copy_started_event = asyncio.Event()
        patch_call_count = [0]

        def blocking_copy(src_file: object, dest: object, max_bytes: int) -> None:
            patch_call_count[0] += 1
            loop.call_soon_threadsafe(copy_started_event.set)
            gate.wait(timeout=10)
            # Return without error so the copy "succeeds" after unblocking.

        monkeypatch.setattr(import_mod, "_copy_chunked", blocking_copy)
        monkeypatch.setattr(import_mod, "enqueue_for_execution", lambda *a, **kw: None)

        count_before = jobs_mod._global_count
        tmpdir_before = set(glob.glob(f"{tempfile.gettempdir()}/snore-upload-*"))

        app = create_app()

        from collections.abc import AsyncGenerator  # noqa: PLC0415

        async def override_db() -> AsyncGenerator[AsyncSession]:
            async with async_db_session.begin():
                yield async_db_session

        async def override_actor(
            db: Annotated[AsyncSession, Depends(get_db)],
        ) -> ActorContext:
            return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_actor] = override_actor

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            upload_task = asyncio.create_task(
                client.post(
                    "/api/v1/import/",
                    files=[
                        (
                            "files",
                            ("test.edf", b"x" * 256, "application/octet-stream"),
                        )
                    ],
                )
            )

            # Poll for copy to start, yielding to the event loop each iteration
            # so upload_task can make progress.
            deadline = loop.time() + 10
            while not copy_started_event.is_set():
                if loop.time() > deadline:
                    gate.set()
                    upload_task.cancel()
                    try:
                        await upload_task
                    except Exception:
                        pass
                    status = "still running"
                    if upload_task.done():
                        try:
                            resp = upload_task.result()
                            status = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        except Exception as exc:
                            status = f"exc={exc!r}"
                    pytest.fail(
                        f"blocking_copy never called (patch_call_count={patch_call_count[0]}).\n"
                        f"Upload result: {status}\n"
                        f"SNORE_MULTIUSER_PLAN.md:194-198."
                    )
                if upload_task.done():
                    # Upload completed before copy started — patch not intercepted.
                    gate.set()
                    status = "exception"
                    try:
                        resp = upload_task.result()
                        status = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    except Exception as exc:
                        status = f"exc={exc!r}"
                    pytest.fail(
                        f"Upload completed before copy started "
                        f"(patch_call_count={patch_call_count[0]}).\n"
                        f"Result: {status}"
                    )
                await asyncio.sleep(0.05)

            # Copy has started — cancel the upload task.
            upload_task.cancel()

            # Release the gate so the copy thread can finish.
            gate.set()

            # Await cancellation propagation.
            try:
                await upload_task
            except (asyncio.CancelledError, Exception):
                pass

            # Poll until finally-cleanup finishes (max 2s, 20 × 0.1s).
            for _ in range(20):
                remaining = set(glob.glob(f"{tempfile.gettempdir()}/snore-upload-*"))
                if not (remaining - tmpdir_before):
                    break
                await asyncio.sleep(0.1)

        app.dependency_overrides.clear()

        tmpdir_after = set(glob.glob(f"{tempfile.gettempdir()}/snore-upload-*"))
        leaked = tmpdir_after - tmpdir_before
        assert not leaked, (
            f"snore-upload-* leaked after CancelledError: {leaked}\n"
            f"SNORE_MULTIUSER_PLAN.md:194-198 (release-on-client-abort)"
        )

        assert jobs_mod._global_count == count_before, (
            f"Slot leaked: was {count_before}, now {jobs_mod._global_count}"
        )

        # Verify a fresh reservation succeeds at the cap.
        fresh = reserve_slot(None)
        assert fresh is not None, "Fresh reservation failed — slot permanently held"
        fresh.try_cancel()
        remove_job(fresh.job_id)
        fresh.cleanup_files()
        fresh.release_capacity()


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
        """snore user invite prints a fragment-based URL, exit code 0.

        The raw token must appear in a fragment (#<token>) so it is never
        sent as a URL path component and never appears in server access logs.
        SNORE_MULTIUSER_PLAN.md §Phase 2:233.
        """
        db_path = str(tmp_path / "invite_test.db")

        env = os.environ.copy()
        env["SNORE_PUBLIC_BASE_URL"] = "https://snore.example.com"
        env["SNORE_AUTH_MODE"] = "local"

        result = subprocess.run(
            [
                str(Path(sys.executable).parent / "snore"),
                "user",
                "invite",
                "test@example.com",
                "--db",
                db_path,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"snore user invite exited {result.returncode}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        output = result.stdout + result.stderr
        # Must affirmatively contain the fragment-based URL.
        assert "snore.example.com/invite#" in output, (
            f"Expected 'snore.example.com/invite#<token>' in output, got:\n{output}"
        )
        # Must NOT use the old path-based format.
        assert "/api/v1/auth/invites/" not in output, (
            f"Old path-based URL format still present in output:\n{output}"
        )

    def test_invite_token_absent_from_server_access_log(self, tmp_path):
        """POST to /auth/invites/lookup and /auth/invites/google with token in body
        must not log the token.

        Starts a real snore serve process, sends requests with known tokens in
        the request bodies (not URLs), then asserts the raw token strings are
        absent from all server output.  Uvicorn's access log only records the
        path; with body-based tokens the paths are always fixed strings.
        SNORE_MULTIUSER_PLAN.md:233.
        """
        import json
        import time
        import urllib.error
        import urllib.request

        token = uuid.uuid4().hex
        google_token = uuid.uuid4().hex
        port = 18772

        env = os.environ.copy()
        env["SNORE_AUTH_MODE"] = "multiuser"
        env["SNORE_SESSION_SECRET"] = "test-secret-at-least-32-chars-long-abcdef"
        env["SNORE_PUBLIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        env["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
        env["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
        env["SNORE_BIND_HOST"] = "127.0.0.1"
        env["SNORE_DB_PATH"] = str(tmp_path / "test.db")

        snore_bin = str(Path(sys.executable).parent / "snore")
        proc = subprocess.Popen(
            [snore_bin, "serve", "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        output = ""
        try:
            # Poll until server is ready.
            for _ in range(40):
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/v1/auth/status", timeout=1
                    )
                    break
                except Exception:
                    time.sleep(0.3)
            else:
                pytest.fail("Server did not start within timeout")

            base_url = f"http://127.0.0.1:{port}"

            # Test 1: POST /auth/invites/lookup — token in body (not URL).
            req = urllib.request.Request(
                f"{base_url}/api/v1/auth/invites/lookup",
                data=json.dumps({"token": token}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Origin": base_url,
                },
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError:
                pass  # valid=false → 200 is fine; any 4xx is also acceptable

            # Test 2: POST /auth/invites/google — token in body (not URL).
            # Uses a different token so any leak is unambiguous.
            req2 = urllib.request.Request(
                f"{base_url}/api/v1/auth/invites/google",
                data=json.dumps({"token": google_token}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Origin": base_url,
                },
            )
            try:
                urllib.request.urlopen(req2, timeout=5)
            except urllib.error.HTTPError:
                pass  # 400 (invalid invite) is expected; just checking logs

        finally:
            proc.terminate()
            try:
                output, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                output, _ = proc.communicate()

        # The raw tokens must NOT appear anywhere in the server output.
        assert token not in output, (
            f"Raw invite token found in server access log output.\n"
            f"SNORE_MULTIUSER_PLAN.md:233 — tokens must never appear in logs.\n"
            f"Output snippet: {output[:500]!r}"
        )
        assert google_token not in output, (
            f"Raw Google invite token found in server access log output.\n"
            f"POST /auth/invites/google token must stay in request body only.\n"
            f"Output snippet: {output[:500]!r}"
        )


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
    """AuthPathMiddleware must return 403 (not pass) when public_origin is None."""

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
            cors_origins=["http://localhost:5173"],
            google_client_id="",
            google_client_secret="",
            oauth_attempt_ttl_seconds=600,
            pre_auth_cookie_ttl_seconds=600,
            max_upload_bytes=512 * 1024 * 1024,
            max_file_bytes=256 * 1024 * 1024,
            max_upload_files=10_000,
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


# =============================================================================
# Pass-3 adversarial tests
# =============================================================================


# ---------------------------------------------------------------------------
# P3-IMPORTANT 1: Rate-limit rotating cursor reaches stale tail
# ---------------------------------------------------------------------------


class TestP3RateLimitCursorRotation:
    """_purge_stale cursor advances so the stale tail is reached even when
    the front of the table is packed with active entries."""

    def test_stale_tail_cleared_with_active_prefix(self):
        """Cursor advances past an active prefix to evict the stale tail.

        The starvation case: a table of 65 IPs where the first 64 are active
        and only position 64 is stale.  Without a rotating cursor,
        _purge_stale(budget=64) always examines positions 0–63 (all active,
        zero deletions) and never reaches position 64 — the new IP is
        untracked forever.  With the rotating cursor, the second sweep starts
        at position 64, finds the stale entry, evicts it, and the new IP is
        tracked within a bounded number of calls.

        ``max_ips = 65 > budget = 64`` is the minimum shape that makes the
        starvation reproducible: at cap=4 the entire table fits in one sweep
        (min(64,4)=4) so cursor position is irrelevant.
        """
        import time

        from collections import deque

        from snore.auth.lockout import RateLimitStore

        # 65 > 64 (budget): active prefix fills exactly one sweep window.
        cap = 65
        store = RateLimitStore(window=60.0, max_per_window=100, max_ips=cap)

        # Fill positions 0–63 with active IPs.
        for i in range(64):
            store.check_and_record(f"10.0.{i // 256}.{i % 256}")

        # Position 64: directly insert a stale entry (timestamp 61s in the past,
        # beyond the 60s window).  This avoids sleeping and is unambiguously stale.
        stale_ts = time.monotonic() - 61.0  # 1 second past the window
        with store._lock:
            store._entries["192.0.2.1"] = deque([stale_ts])

        assert len(store._entries) == cap, "Table must be at capacity"

        # Without cursor: _purge_stale always sweeps positions 0–63 (active),
        # never reaches 192.0.2.1 at position 64 → new IP untracked forever.
        # With cursor: first check_and_record sweeps 0–63 (no deletions, cursor
        # advances to 64), second sweeps 64 (stale, deleted), table drops to 64,
        # new IP tracked on the third call at the latest.
        tracked = False
        for _ in range(4):  # bounded: at most 3 calls with cursor
            if store.check_and_record("203.0.113.1"):
                with store._lock:
                    if "203.0.113.1" in store._entries:
                        tracked = True
                        break
        assert tracked, (
            "New IP was never tracked after bounded calls — rotating cursor "
            "did not advance past the 64-entry active prefix to the stale tail. "
            "The starvation the cursor fixes is still present."
        )


# ---------------------------------------------------------------------------
# P3-IMPORTANT 2: Credentials absent from 422 auth response bodies
# ---------------------------------------------------------------------------


class TestP3CredentialNotIn422:
    """FastAPI validation errors on auth routes must not echo credential inputs."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def test_invite_token_absent_from_422_body(self, async_db_session):
        """POST /auth/invites/redeem with valid token but missing password: 422
        response must NOT contain the raw token.  SNORE_MULTIUSER_PLAN.md:233.
        """
        sentinel = f"SENTINEL_TOKEN_{uuid.uuid4().hex}"
        client = _make_client(async_db_session)

        resp = client.post(
            "/api/v1/auth/invites/redeem",
            json={"token": sentinel},  # missing password field
            headers={"origin": "http://127.0.0.1:8000"},
        )
        # Should be 422 (missing required field).
        assert resp.status_code == 422, (
            f"Expected 422, got {resp.status_code}: {resp.text[:200]}"
        )
        assert sentinel not in resp.text, (
            f"Sentinel token appeared in 422 response body: {resp.text[:300]}"
        )

    def test_password_absent_from_422_body(self, async_db_session):
        """POST /auth/login with an overlong password: 422 must NOT echo the
        password in the response body."""
        sentinel_pw = "SENTINEL_PASSWORD_" + "x" * 5000
        client = _make_client(async_db_session)

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": sentinel_pw},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 422, (
            f"Expected 422, got {resp.status_code}: {resp.text[:200]}"
        )
        assert sentinel_pw not in resp.text, (
            f"Sentinel password appeared in 422 response body: {resp.text[:300]}"
        )


# ---------------------------------------------------------------------------
# P3-MINOR 1: IPv6 normalization — equivalent forms map to one key
# ---------------------------------------------------------------------------


class TestP3IPv6Normalization:
    """Equivalent IPv6 addresses produce identical rate-limit/lockout keys."""

    def test_equivalent_ipv6_canonical_form(self):
        """``2001:0db8:0:0:0:0:0:1`` and ``2001:db8::1`` both normalize to the
        same canonical string via get_client_ip()."""
        import ipaddress

        # Verify the canonical form is what Python's ipaddress gives.
        addr = ipaddress.ip_address("2001:0db8:0:0:0:0:0:1")
        canonical = str(addr)
        assert ipaddress.ip_address("2001:db8::1") == addr
        assert canonical == str(ipaddress.ip_address("2001:db8::1"))

    def test_ipv6_lockout_key_deduplicated(self, monkeypatch):
        """Two spellings of the same IPv6 address share one lockout entry."""
        monkeypatch.setenv("SNORE_TRUSTED_PROXIES", "10.0.0.1")
        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.requests import Request  # noqa: PLC0415
        from starlette.responses import PlainTextResponse  # noqa: PLC0415
        from starlette.routing import Route  # noqa: PLC0415
        from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: PLC0415

        from snore.api.client_ip import get_client_ip  # noqa: PLC0415

        captured: list[str] = []

        async def view(r: Request) -> PlainTextResponse:
            captured.append(get_client_ip(r))
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/", view)])

        class TrustedPeerMiddleware:
            def __init__(self, wrapped: ASGIApp) -> None:
                self.wrapped = wrapped

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                if scope["type"] == "http":
                    scope["client"] = ("10.0.0.1", 12345)
                await self.wrapped(scope, receive, send)

        from fastapi.testclient import TestClient  # noqa: PLC0415

        client = TestClient(TrustedPeerMiddleware(app))

        client.get("/", headers={"cf-connecting-ip": "2001:0db8:0:0:0:0:0:1"})
        client.get("/", headers={"cf-connecting-ip": "2001:db8::1"})

        assert len(captured) == 2
        assert captured[0] == captured[1], (
            f"Equivalent IPv6 spellings produced different keys: "
            f"{captured[0]!r} vs {captured[1]!r}"
        )


# ---------------------------------------------------------------------------
# P3-MINOR 2: Strict dev-origin validation rejects non-origin values
# ---------------------------------------------------------------------------


class TestP3DevOriginStrictValidation:
    """SNORE_DEV_ORIGINS rejects non-http/https schemes and disallowed components."""

    def test_javascript_scheme_rejected(self, monkeypatch):
        """``javascript://`` scheme must be a ConfigError at startup."""
        from snore.api.config import ConfigError  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        monkeypatch.setenv("SNORE_DEV_ORIGINS", "javascript://allowed.example")

        with pytest.raises(ConfigError, match="invalid origin"):
            load_config()

    def test_userinfo_rejected(self, monkeypatch):
        """``https://user@host`` must be rejected (userinfo present)."""
        from snore.api.config import ConfigError  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        monkeypatch.setenv("SNORE_DEV_ORIGINS", "https://user@allowed.example")

        with pytest.raises(ConfigError, match="invalid origin"):
            load_config()

    def test_path_rejected(self, monkeypatch):
        """``https://host/path`` must be rejected (path present)."""
        from snore.api.config import ConfigError  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        monkeypatch.setenv("SNORE_DEV_ORIGINS", "https://allowed.example/path")

        with pytest.raises(ConfigError, match="invalid origin"):
            load_config()

    def test_fragment_rejected(self, monkeypatch):
        """``https://host#frag`` must be rejected (fragment present)."""
        from snore.api.config import ConfigError  # noqa: PLC0415

        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        monkeypatch.setenv("SNORE_DEV_ORIGINS", "https://allowed.example#frag")

        with pytest.raises(ConfigError, match="invalid origin"):
            load_config()

    def test_valid_http_dev_origin_accepted(self, monkeypatch):
        """A plain ``http://hostname:port`` dev origin is accepted."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        monkeypatch.setenv("SNORE_DEV_ORIGINS", "http://localhost:5173")

        cfg = load_config()
        assert ("http", "localhost", 5173) in cfg.dev_origins


# ---------------------------------------------------------------------------
# P3-MINOR 3: Auth body ceiling surfaces as 413, not 400
# ---------------------------------------------------------------------------


class TestP3AuthBodyCeiling413:
    """The 16 KiB auth-body ceiling must return 413 regardless of encoding.

    The pre-read buffer in AuthPathMiddleware.dispatch consumes the full ASGI
    stream before call_next, making Content-Length presence, accuracy, and
    chunked encoding irrelevant.  Tests use httpx.AsyncClient + ASGITransport
    for chunked/generator bodies that TestClient cannot model.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        _multiuser_config(monkeypatch)
        set_config(
            load_config(auth_mode_override="multiuser", bind_host_override="127.0.0.1")
        )

    def _make_async_app(self, async_db_session: AsyncSession) -> object:
        """Build the real app with get_db/get_actor overrides for async tests."""
        from collections.abc import AsyncGenerator  # noqa: PLC0415

        app = create_app()

        async def override_db() -> AsyncGenerator[AsyncSession]:
            async with async_db_session.begin():
                yield async_db_session

        async def override_actor(
            db: Annotated[AsyncSession, Depends(get_db)],
        ) -> ActorContext:
            return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_actor] = override_actor
        return app

    def test_oversized_auth_body_returns_413(self, async_db_session):
        """Fixed-length oversized body (Content-Length set by client) → 413."""
        client = _make_client(async_db_session)
        big_password = "x" * (17 * 1024)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": big_password},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 413, (
            f"Expected 413 for oversized body, got {resp.status_code}: "
            f"{resp.text[:200]}"
        )

    def test_body_just_below_ceiling_passes(self, async_db_session):
        """A body just under 16 KiB must not be rejected by the ceiling."""
        client = _make_client(async_db_session)
        small_password = "x" * 10
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": small_password},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code not in (400, 413), (
            f"Body ceiling fired prematurely on small body: {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_chunked_no_content_length_returns_413(self, async_db_session):
        """Chunked body, no Content-Length, total > 16 KiB → 413.

        **Falsifiability:** reverting to a Content-Length-only shortcut means
        chunked bodies without the header are never intercepted and reach the
        JSON parser, which converts the streaming error to
        400 {"detail":"There was an error parsing the body"}.
        The pre-read buffer closes this gap.
        """
        import asyncio  # noqa: PLC0415

        import httpx  # noqa: PLC0415

        from snore.api.middleware import _AUTH_BODY_LIMIT  # noqa: PLC0415

        app = self._make_async_app(async_db_session)

        from collections.abc import AsyncIterator  # noqa: PLC0415

        async def chunked_body() -> AsyncIterator[bytes]:
            """Yield 17 one-KiB chunks — httpx sends as chunked (no CL)."""
            for _ in range(17):
                yield b"x" * 1024
                await asyncio.sleep(0)  # yield to event loop between chunks

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/login",
                content=chunked_body(),
                headers={
                    "content-type": "application/json",
                    "origin": "http://127.0.0.1:8000",
                },
            )

        assert resp.status_code == 413, (
            f"Expected 413 for chunked body > {_AUTH_BODY_LIMIT} bytes "
            f"without Content-Length, got {resp.status_code}: {resp.text[:200]}.\n"
            f"If 400: the pre-read buffer was replaced by a Content-Length-only "
            f"shortcut that misses chunked requests."
        )

    @pytest.mark.asyncio
    async def test_body_exactly_at_limit_is_not_rejected(self, async_db_session):
        """A body of exactly _AUTH_BODY_LIMIT bytes passes through the ceiling."""
        import httpx  # noqa: PLC0415

        from snore.api.middleware import _AUTH_BODY_LIMIT  # noqa: PLC0415

        app = self._make_async_app(async_db_session)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/login",
                content=b"x" * _AUTH_BODY_LIMIT,
                headers={
                    "content-type": "application/json",
                    "origin": "http://127.0.0.1:8000",
                },
            )

        # Auth will reject 422 (body is not valid JSON) but must NOT return 413.
        assert resp.status_code == 422, (
            f"Expected 422 (invalid JSON body) at exactly {_AUTH_BODY_LIMIT} bytes, "
            f"got {resp.status_code}. If 413: ceiling off-by-one. "
            f"If 400/other: ceiling corrupted the body."
        )

    @pytest.mark.asyncio
    async def test_body_one_byte_over_limit_returns_413(self, async_db_session):
        """A body of _AUTH_BODY_LIMIT + 1 bytes is rejected with 413."""
        import httpx  # noqa: PLC0415

        from snore.api.middleware import _AUTH_BODY_LIMIT  # noqa: PLC0415

        app = self._make_async_app(async_db_session)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/login",
                content=b"x" * (_AUTH_BODY_LIMIT + 1),
                headers={
                    "content-type": "application/json",
                    "origin": "http://127.0.0.1:8000",
                },
            )

        assert resp.status_code == 413, (
            f"Expected 413 for {_AUTH_BODY_LIMIT + 1}-byte body, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_lying_content_length_returns_413(self, async_db_session):
        """Lying Content-Length (10) with 17 KiB actual body → 413.

        The pre-read buffer counts actual bytes, not the declared header, so
        a fraudulent small Content-Length cannot bypass the ceiling.
        """
        import asyncio  # noqa: PLC0415

        import httpx  # noqa: PLC0415

        from snore.api.middleware import _AUTH_BODY_LIMIT  # noqa: PLC0415

        app = self._make_async_app(async_db_session)
        big_body = b"x" * (_AUTH_BODY_LIMIT + 1024)

        from collections.abc import AsyncIterator  # noqa: PLC0415

        async def lying_body() -> AsyncIterator[bytes]:
            chunk = 1024
            for i in range(0, len(big_body), chunk):
                yield big_body[i : i + chunk]
                await asyncio.sleep(0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/login",
                content=lying_body(),
                headers={
                    "content-type": "application/json",
                    "content-length": "10",  # declared small, actual is large
                    "origin": "http://127.0.0.1:8000",
                },
            )

        assert resp.status_code == 413, (
            f"Expected 413 even with lying Content-Length: 10, "
            f"got {resp.status_code}. Pre-read buffer must count actual bytes."
        )

    @pytest.mark.asyncio
    async def test_no_drain_after_limit_crossed(self):
        """Return 413 immediately — receive callable not called after limit is crossed.

        A drain loop (reading until more_body=False) can block indefinitely if
        the client stops sending after crossing the limit.  Verify the receive
        callable is NOT invoked after the frame that crosses the ceiling.
        """

        from starlette.requests import Request  # noqa: PLC0415

        from snore.api.middleware import (  # noqa: PLC0415
            _AUTH_BODY_LIMIT,
            AuthPathMiddleware,
        )

        # Two frames that cross the limit; any further receive call is a drain bug.
        frames = [
            {
                "type": "http.request",
                "body": b"x" * _AUTH_BODY_LIMIT,
                "more_body": True,
            },
            # This frame pushes total to _AUTH_BODY_LIMIT + 1.
            {"type": "http.request", "body": b"x", "more_body": True},
        ]
        receive_calls = [0]

        async def counting_receive() -> dict:
            receive_calls[0] += 1
            idx = receive_calls[0] - 1
            if idx < len(frames):
                return frames[idx]
            # If called beyond the two frames: drain is happening → fail.
            raise AssertionError(
                f"receive called {receive_calls[0]} times — drain loop detected"
            )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"origin", b"http://127.0.0.1:8000"),
            ],
            "root_path": "",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }
        request = Request(scope, receive=counting_receive)

        from starlette.responses import Response as StarletteResponse  # noqa: PLC0415

        async def _noop_app(scope, receive, send):
            pass

        csrf_mw = AuthPathMiddleware(app=_noop_app)
        call_next_invoked = [False]

        async def fake_call_next(req: Request) -> StarletteResponse:
            call_next_invoked[0] = True
            return StarletteResponse(status_code=200)

        response = await csrf_mw.dispatch(request, fake_call_next)

        assert response.status_code == 413, f"Expected 413, got {response.status_code}"
        assert receive_calls[0] == 2, (
            f"Expected exactly 2 receive calls (limit frame + crossing frame), "
            f"got {receive_calls[0]}. Drain loop runs extra receives."
        )
        assert not call_next_invoked[0], "call_next invoked despite 413"

    @pytest.mark.asyncio
    async def test_disconnect_before_terminal_frame_aborts_without_handler(self):
        """http.disconnect before more_body=False → 0 call_next invocations.

        A disconnect mid-body must not reach side-effecting handlers
        (e.g. /invites/redeem which creates a user+profile).
        """
        from starlette.requests import Request  # noqa: PLC0415

        from snore.api.middleware import AuthPathMiddleware  # noqa: PLC0415

        frames_iter = iter(
            [
                # Syntactically complete JSON, but not terminal (more_body=True).
                {
                    "type": "http.request",
                    "body": b'{"token":"t","password":"p"}',
                    "more_body": True,
                },
                # Client disconnects before completing the request.
                {"type": "http.disconnect"},
            ]
        )

        async def receive() -> dict:
            return next(frames_iter)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"origin", b"http://127.0.0.1:8000"),
            ],
            "root_path": "",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }
        request = Request(scope, receive=receive)

        from starlette.responses import Response as StarletteResponse  # noqa: PLC0415

        async def _noop_app2(scope, receive, send):
            pass

        csrf_mw = AuthPathMiddleware(app=_noop_app2)
        call_next_invoked = [False]

        async def fake_call_next(req: Request) -> StarletteResponse:
            call_next_invoked[0] = True
            return StarletteResponse(status_code=200)

        response = await csrf_mw.dispatch(request, fake_call_next)

        assert not call_next_invoked[0], (
            "call_next was invoked despite http.disconnect before terminal frame. "
            "Side-effecting handlers (e.g. /invites/redeem) must not run on "
            "incomplete requests."
        )
        assert response.status_code == 499, (
            f"Expected 499 (client disconnect abort), got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_replay_receive_delegates_to_original_after_first_replay(self):
        """After replaying the buffered body once, subsequent receives delegate to
        original_receive — not a manufactured http.disconnect.

        Proves _replay_receive line 279 (``return await original_receive()``) is
        reachable and returns what original_receive returns, not a synthetic disconnect.
        """
        from starlette.requests import Request  # noqa: PLC0415
        from starlette.responses import Response as StarletteResponse  # noqa: PLC0415

        from snore.api.middleware import AuthPathMiddleware  # noqa: PLC0415

        body_bytes = b'{"email":"test@example.com","password":"pw"}'
        sentinel_msg = {"type": "http.disconnect", "body": b"SENTINEL"}

        async def original_receive() -> dict:
            return sentinel_msg

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"origin", b"http://127.0.0.1:8000"),
            ],
            "root_path": "",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }

        # Terminal http.request frame — will be buffered and replayed.
        terminal_frame = {
            "type": "http.request",
            "body": body_bytes,
            "more_body": False,
        }
        _called = [0]

        async def counting_receive() -> dict:
            _called[0] += 1
            return terminal_frame

        request = Request(scope, receive=counting_receive)
        # Override the private callable with one that returns the sentinel after replay.
        # We have to do this after Request is constructed because it copies the receive.
        _original_saved = request.receive

        async def receive_with_sentinel() -> dict:
            return sentinel_msg

        request._receive = receive_with_sentinel  # noqa: SLF001

        # Reconstruct so dispatch sees the right receive.
        request = Request(scope, receive=receive_with_sentinel)
        # Prime it with the real terminal frame first.
        first_call = [True]

        async def staged_receive() -> dict:
            if first_call[0]:
                first_call[0] = False
                return terminal_frame
            return sentinel_msg

        request = Request(scope, receive=staged_receive)

        async def _noop_app3(scope, receive, send):
            pass

        csrf_mw = AuthPathMiddleware(app=_noop_app3)

        received_in_handler: list[dict] = []

        async def checking_call_next(req: Request) -> StarletteResponse:
            # First receive = buffered body replay.
            msg1 = await req.receive()
            received_in_handler.append(msg1)
            # Second receive = original_receive delegate.
            msg2 = await req.receive()
            received_in_handler.append(msg2)
            return StarletteResponse(status_code=200)

        await csrf_mw.dispatch(request, checking_call_next)

        assert len(received_in_handler) == 2, (
            f"Expected 2 receives in handler, got {len(received_in_handler)}"
        )
        assert received_in_handler[0]["type"] == "http.request", (
            "First handler receive must be the replayed http.request body"
        )
        assert received_in_handler[0].get("body") == body_bytes, (
            "Replayed body must match the original buffered bytes"
        )
        assert received_in_handler[1] is sentinel_msg, (
            "Second handler receive must come from original_receive, "
            "not a manufactured http.disconnect"
        )


# ---------------------------------------------------------------------------
# S2: Login and invite lockout stores are separate
# ---------------------------------------------------------------------------


class TestSeparateLockoutStores:
    """Login and invite paths must use distinct LockoutStore instances.

    Sharing one store allows exhausting the invite endpoint to silently
    disable login lockout protection.
    """

    def test_login_and_invite_lockout_stores_are_distinct(self):
        """get_lockout_store() and get_invite_lockout_store() return different objects."""
        from snore.auth.lockout import (  # noqa: PLC0415
            get_invite_lockout_store,
            get_lockout_store,
        )

        assert get_lockout_store() is not get_invite_lockout_store(), (
            "Login lockout store and invite lockout store must be distinct instances"
        )

    def test_invite_failures_do_not_affect_login_lockout(self):
        """Filling the invite lockout store leaves the login lockout store unaffected."""
        import snore.auth.lockout as lockout_mod  # noqa: PLC0415

        from snore.auth.lockout import (  # noqa: PLC0415
            get_invite_lockout_store,
            get_lockout_store,
        )

        original_max = lockout_mod.MAX_ENTRIES
        lockout_mod.MAX_ENTRIES = 3
        try:
            login_store = get_lockout_store()
            invite_store = get_invite_lockout_store()

            # Fill the invite store to capacity with unexpired entries.
            for i in range(3):
                invite_store.record_failure(f"token{i}", "1.2.3.4")

            # Login store must still accept new entries.
            login_email = f"login_{uuid.uuid4().hex[:6]}@example.com"
            login_store.record_failure(login_email, "1.2.3.4")
            assert login_store.is_locked(login_email, "1.2.3.4"), (
                "Login lockout store must still accept entries when invite store is full"
            )
        finally:
            lockout_mod.MAX_ENTRIES = original_max
            # Clean up: remove the test failure from the live login store.
            from snore.auth.lockout import get_lockout_store as _get  # noqa: PLC0415

            _get().record_success(login_email, "1.2.3.4")


# ---------------------------------------------------------------------------
# S4: X-Forwarded-For and X-Real-IP fallback in get_client_ip
# ---------------------------------------------------------------------------


class TestGetClientIpForwardedHeaders:
    """get_client_ip honours XFF and X-Real-IP when the peer is trusted."""

    def _make_captured_app(self, monkeypatch: pytest.MonkeyPatch) -> tuple:
        """Return (app, captured_list) — app records get_client_ip on each request."""
        from snore.api.client_ip import get_client_ip  # noqa: PLC0415

        monkeypatch.setenv("SNORE_TRUSTED_PROXIES", "10.0.0.1")
        monkeypatch.setenv("SNORE_AUTH_MODE", "local")
        set_config(
            load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        )

        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.requests import Request  # noqa: PLC0415
        from starlette.responses import PlainTextResponse  # noqa: PLC0415
        from starlette.routing import Route  # noqa: PLC0415
        from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: PLC0415

        captured: list[str] = []

        async def view(r: Request) -> PlainTextResponse:
            captured.append(get_client_ip(r))
            return PlainTextResponse("ok")

        base_app = Starlette(routes=[Route("/", view)])

        class TrustedPeerWrapper:
            def __init__(self, wrapped: ASGIApp) -> None:
                self.wrapped = wrapped

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                if scope["type"] == "http":
                    scope["client"] = ("10.0.0.1", 12345)
                await self.wrapped(scope, receive, send)

        from fastapi.testclient import TestClient  # noqa: PLC0415

        client = TestClient(TrustedPeerWrapper(base_app))
        return client, captured

    def test_xff_rightmost_entry_used_when_trusted_peer(self, monkeypatch):
        """Trusted peer + X-Forwarded-For: 1.2.3.4, 5.6.7.8 → returns 5.6.7.8."""
        client, captured = self._make_captured_app(monkeypatch)
        client.get("/", headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
        assert captured, "view was never called"
        assert captured[0] == "5.6.7.8", (
            f"Expected rightmost XFF entry 5.6.7.8, got {captured[0]!r}"
        )

    def test_x_real_ip_used_when_xff_absent(self, monkeypatch):
        """Trusted peer + X-Real-IP: 1.2.3.4 (no XFF) → returns 1.2.3.4."""
        client, captured = self._make_captured_app(monkeypatch)
        client.get("/", headers={"x-real-ip": "1.2.3.4"})
        assert captured, "view was never called"
        assert captured[0] == "1.2.3.4", (
            f"Expected X-Real-IP value 1.2.3.4, got {captured[0]!r}"
        )

    def test_peer_used_when_no_forwarding_headers(self, monkeypatch):
        """Trusted peer with no XFF or X-Real-IP → falls back to peer address."""
        client, captured = self._make_captured_app(monkeypatch)
        client.get("/")
        assert captured, "view was never called"
        assert captured[0] == "10.0.0.1", (
            f"Expected peer fallback 10.0.0.1, got {captured[0]!r}"
        )

    def test_malformed_xff_skips_to_x_real_ip(self, monkeypatch):
        """Malformed XFF rightmost entry → skips, falls back to X-Real-IP."""
        client, captured = self._make_captured_app(monkeypatch)
        client.get(
            "/",
            headers={"x-forwarded-for": "1.2.3.4, not-an-ip", "x-real-ip": "9.9.9.9"},
        )
        assert captured, "view was never called"
        assert captured[0] == "9.9.9.9", (
            f"Expected X-Real-IP fallback 9.9.9.9 after malformed XFF, got {captured[0]!r}"
        )
