"""Integration tests for the SNORE MCP server embedded into the FastAPI app.

Architecture: uvicorn in a daemon thread serves create_app() with a fully
configured multiuser env.  snore.api.mcp_embed._make_mcp_auth_provider is
monkeypatched to return a JWTVerifier so no real Google credentials are needed.

Transport choice: uvicorn in a daemon thread (own event loop).
- Exercises the full ASGI lifespan: DB init, MCP sub-app lifespan chaining,
  worker startup, and clean teardown.
- The thread's event loop is independent of pytest's asyncio event loop; they
  communicate through real OS-level TCP sockets.
- ASGITransport is NOT used: it does not run the ASGI lifespan.

Scope: module-scoped server + DB for speed; tests share the running server.

DB lifecycle:
  1. create_app() lifespan calls init_database_from_url → schema + engine.
  2. After the server is ready, _seed_sync() inserts test rows via a plain
     synchronous SQLAlchemy session on the same SQLite file.
  3. Tests make HTTP requests to the running server.
  4. On fixture teardown, uvicorn stops; its lifespan runs cleanup.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

from collections.abc import Generator
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest
import uvicorn

from fastmcp import Client
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

import snore.api.mcp_embed as _mcp_embed_mod

from snore.database.models import AuthIdentity, Day, Device, Profile, Session, User

# Snapshot the real _make_mcp_auth_provider at module import time, before any
# test fixture can patch it.  _discovery_server_url uses this to guarantee it
# gets the real GoogleProvider even when _server_base_url's patch is active.
_REAL_MAKE_AUTH_PROVIDER = _mcp_embed_mod._make_mcp_auth_provider

# ---------------------------------------------------------------------------
# Override the integration conftest's autouse DB cleanup between tests.
# The module-scoped server owns the global DB engine for the module's lifetime;
# cleanup_database() mid-module would dispose the shared engine.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_database_state() -> None:
    """Override integration conftest: module-scoped server manages DB lifecycle."""
    return


# ---------------------------------------------------------------------------
# Stable subject values
# ---------------------------------------------------------------------------

_RUN_ID = uuid.uuid4().hex[:8]
_SUB_MEMBER = f"sub-member-{_RUN_ID}"
_TEST_AUDIENCE = "https://mcp.embedded.test"
_SESSION_SECRET = "test-secret-at-least-32-chars-zzzz-embedded"
_GOOGLE_CLIENT_ID = "dummy-client-id"
_GOOGLE_CLIENT_SECRET = "dummy-client-secret"


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------


def _mint_token(key_pair: RSAKeyPair, sub: str, expires_in: int = 3600) -> str:
    return key_pair.create_token(
        subject=sub,
        expires_in_seconds=expires_in,
        audience=_TEST_AUDIENCE,
    )


def _mcp_client(url: str, token: str) -> Client:
    return Client(transport=StreamableHttpTransport(url=url, auth=BearerAuth(token)))


# ---------------------------------------------------------------------------
# Sync DB seeding — called after the server has initialized the schema
# ---------------------------------------------------------------------------


def _seed_sync(db_path: str) -> None:
    """Insert a member user + profile + auth identity for MCP auth tests."""
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    factory: sessionmaker[OrmSession] = sessionmaker(bind=engine)
    db = factory()
    try:
        user = User(
            canonical_email=f"member_{uuid.uuid4().hex[:6]}@test.example",
            role="member",
        )
        db.add(user)
        db.flush()

        profile = Profile(user_id=user.id, name="Member Profile")
        db.add(profile)
        db.flush()

        db.add(AuthIdentity(user_id=user.id, provider="google", subject=_SUB_MEMBER))

        device = Device(
            profile_id=profile.id,
            manufacturer="EmbedMfr",
            model="EmbedMdl",
            serial_number=f"EMB{uuid.uuid4().hex[:8]}",
        )
        db.add(device)
        db.flush()

        day_date = date(2024, 6, 15)
        day = Day(device_id=device.id, date=day_date, total_therapy_hours=7.5)
        db.add(day)
        db.flush()
        db.add(
            Session(
                device_id=device.id,
                day_id=day.id,
                device_session_id=f"s{uuid.uuid4().hex[:8]}",
                start_time=datetime(2024, 6, 15, 22, 0),
                end_time=datetime(2024, 6, 15, 22, 0) + timedelta(hours=7.5),
                duration_seconds=7.5 * 3600,
                enabled=True,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Module-scoped key pair + server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _key_pair() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture(scope="module")
def _verifier(_key_pair: RSAKeyPair) -> JWTVerifier:
    return JWTVerifier(public_key=_key_pair.public_key, audience=_TEST_AUDIENCE)


@pytest.fixture(scope="module")
def _server_base_url(
    tmp_path_factory: pytest.TempPathFactory,
    _verifier: JWTVerifier,
) -> Generator[str]:
    """Start uvicorn serving create_app() with embedded MCP, yield the base URL.

    Monkeypatches snore.api.mcp_embed._make_mcp_auth_provider to return the
    JWTVerifier so no real Google credentials are required.  SNORE_PUBLIC_BASE_URL
    is set to a loopback placeholder — the JWTVerifier ignores the base URL (it
    is only used by the real GoogleProvider to build OAuth metadata), so the
    value is never updated to match the server's ephemeral port.

    The actual port is OS-assigned (port=0) to avoid TOCTOU races.
    """
    import snore.api.mcp_embed as _mcp_embed  # noqa: PLC0415

    db_path = str(tmp_path_factory.mktemp("embedded_mcp") / "test_embedded.db")

    # Patch the auth provider factory before create_app() is called.  The
    # JWTVerifier replaces GoogleProvider so no real Google credentials are needed.
    # SNORE_PUBLIC_BASE_URL is used as the OAuth issuer base URL by the real
    # GoogleProvider; the JWTVerifier ignores it, so any valid URL works here.
    _verifier_ref = _verifier
    original = _mcp_embed._make_mcp_auth_provider

    def _fake_auth_provider(cfg: Any) -> Any:  # noqa: ANN401
        return _verifier_ref

    _mcp_embed._make_mcp_auth_provider = _fake_auth_provider

    import os  # noqa: PLC0415

    # Stash and set environment for this server's create_app() / load_config() call.
    _saved = {}
    env_vars = {
        "SNORE_AUTH_MODE": "multiuser",
        "SNORE_SESSION_SECRET": _SESSION_SECRET,
        "SNORE_PUBLIC_BASE_URL": "http://127.0.0.1",
        "GOOGLE_CLIENT_ID": _GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": _GOOGLE_CLIENT_SECRET,
        "SNORE_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
    }
    for k, v in env_vars.items():
        _saved[k] = os.environ.get(k)
        os.environ[k] = v

    from snore.api.config import reset_config  # noqa: PLC0415

    reset_config()

    from snore.api.app import create_app  # noqa: PLC0415

    app = create_app()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        lifespan="on",
        log_level="error",
    )
    uv = uvicorn.Server(config)

    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20.0
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not uv.started:
        uv.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("uvicorn server failed to start within 20 s")

    port: int = uv.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    # Seed test data after schema is initialized.
    _seed_sync(db_path)

    try:
        yield base_url
    finally:
        uv.should_exit = True
        thread.join(timeout=15)
        # Restore environment and auth provider.
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _mcp_embed._make_mcp_auth_provider = original
        reset_config()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_mcp_no_token_no_origin_returns_401_not_403(
    _server_base_url: str,
) -> None:
    """POST /mcp with no Authorization and no Origin → 401, not 403.

    Without the CSRF exemption for MCP paths this would return 403 because
    the CSRF middleware fires before auth.  The exemption must be in place.
    """
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{_server_base_url}/mcp",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1.0"},
                    },
                    "id": 1,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


async def test_mcp_bearer_auth_can_call_get_data_overview(
    _server_base_url: str, _key_pair: RSAKeyPair
) -> None:
    """Authenticated MCP client can call get_data_overview successfully."""
    token = _mint_token(_key_pair, _SUB_MEMBER)
    mcp_url = f"{_server_base_url}/mcp"
    async with _mcp_client(mcp_url, token) as client:
        result = await client.call_tool("get_data_overview")
    assert not result.is_error
    assert result.structured_content is not None
    devices: list[dict[str, Any]] = result.structured_content.get("devices", [])
    mfrs = {d["manufacturer"] for d in devices}
    assert "EmbedMfr" in mfrs


async def test_cookie_auth_route_without_origin_returns_403(
    _server_base_url: str,
) -> None:
    """POST to a cookie-authenticated API route without Origin → 403.

    This is the CSRF regression guard: the exemption must apply ONLY to MCP
    paths, not to regular API endpoints.
    """
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{_server_base_url}/api/v1/profiles",
            content=json.dumps({"name": "test"}),
            headers={"Content-Type": "application/json"},
        )
    # CSRF check fires before auth for unsafe methods without Origin.
    assert resp.status_code == 403


async def test_health_endpoint_returns_200(_server_base_url: str) -> None:
    """GET /health returns 200 — basic liveness check for the embedded server."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(f"{_server_base_url}/health")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"


# ---------------------------------------------------------------------------
# OAuth discovery metadata tests (real GoogleProvider, static routes)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _discovery_server_url(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[str]:
    """Separate server for testing well-known OAuth discovery routes.

    Uses the REAL GoogleProvider (no monkeypatch) with dummy credentials.
    GoogleProvider's /.well-known/* routes are static — they only return JSON
    documents computed from SNORE_PUBLIC_BASE_URL at app construction time.
    No network calls to Google are made.  The test asserts response status and
    JSON structure only; the issuer URL reflects SNORE_PUBLIC_BASE_URL at
    fixture startup (a loopback placeholder), not the server's ephemeral port.

    Snapshot + restore of _make_mcp_auth_provider: the _server_base_url fixture
    may patch this function.  We temporarily install _REAL_MAKE_AUTH_PROVIDER
    during create_app() to guarantee a real GoogleProvider is built, then restore
    whatever was active before (which may be the JWTVerifier patch).
    """
    import os  # noqa: PLC0415

    db_path = str(tmp_path_factory.mktemp("discovery") / "test_discovery.db")

    _saved = {}
    env_vars = {
        "SNORE_AUTH_MODE": "multiuser",
        "SNORE_SESSION_SECRET": _SESSION_SECRET,
        "SNORE_PUBLIC_BASE_URL": "http://127.0.0.1",
        "GOOGLE_CLIENT_ID": _GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": _GOOGLE_CLIENT_SECRET,
        "SNORE_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
    }
    for k, v in env_vars.items():
        _saved[k] = os.environ.get(k)
        os.environ[k] = v

    from snore.api.config import reset_config  # noqa: PLC0415

    reset_config()

    # Temporarily install the real auth provider (not the JWTVerifier patch from
    # _server_base_url) so that create_app() builds a real GoogleProvider which
    # serves the static /.well-known/* metadata routes.
    prior_provider = _mcp_embed_mod._make_mcp_auth_provider
    _mcp_embed_mod._make_mcp_auth_provider = _REAL_MAKE_AUTH_PROVIDER

    from snore.api.app import create_app  # noqa: PLC0415

    app = create_app()

    _mcp_embed_mod._make_mcp_auth_provider = prior_provider

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        lifespan="on",
        log_level="error",
    )
    uv = uvicorn.Server(config)

    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20.0
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not uv.started:
        uv.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("uvicorn (discovery) server failed to start within 20 s")

    port: int = uv.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    try:
        yield base_url
    finally:
        uv.should_exit = True
        thread.join(timeout=15)
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_config()


async def test_well_known_oauth_authorization_server_returns_json(
    _discovery_server_url: str,
) -> None:
    """GET /.well-known/oauth-authorization-server → 200 JSON, not HTML."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{_discovery_server_url}/.well-known/oauth-authorization-server"
        )
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "json" in ct, f"Expected JSON content-type, got: {ct!r}"
    body = resp.json()
    assert "issuer" in body


async def test_well_known_oauth_protected_resource_returns_json(
    _discovery_server_url: str,
) -> None:
    """GET /.well-known/oauth-protected-resource/mcp → 200 JSON."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{_discovery_server_url}/.well-known/oauth-protected-resource/mcp"
        )
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert "json" in ct, f"Expected JSON content-type, got: {ct!r}"


async def test_unknown_path_returns_404_not_html(_server_base_url: str) -> None:
    """GET /some-unknown-path returns 404 (no ui/dist in CI, no index.html fallback).

    Proves the MCP sub-app's 404 falls through to the real 404 rather than being
    consumed by a catch-all that would return the SPA or wrong content.
    """
    async with httpx.AsyncClient() as http:
        resp = await http.get(f"{_server_base_url}/some-unknown-path-xyz")
    assert resp.status_code == 404
