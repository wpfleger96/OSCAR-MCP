"""Adversarial security tests for Phase 2 auth backend.

Each class corresponds to a finding from Thufir pass-1 review.  Test names
are prefixed with the finding category so failures are easy to triage.

Plan bindings (SNORE_MULTIUSER_PLAN.md):
  §Session & CSRF Design : lines 160-166 (CSRF scope)
  §Upload & Job Resource  : lines 191-198 (bounded chunked copies)
  §Trusted Proxy          : lines 184-189 (rate limiting scope)
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
            f"/api/v1/auth/invites/{token}/redeem",
            json={"password": long_pw},
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
        """GET /auth/invites/{token} (200) has Cache-Control: no-store."""
        client = _make_client(async_db_session)
        resp = client.get(f"/api/v1/auth/invites/{uuid.uuid4().hex}")
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("cache-control", "").lower()
