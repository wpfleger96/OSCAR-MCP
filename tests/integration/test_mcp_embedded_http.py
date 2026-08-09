"""End-to-end integration tests for the MCP server embedded in the FastAPI app.

Serves ``snore.api.app:create_app`` (not a standalone FastMCP app) so the full
stack is exercised: FastAPI lifespan (DB init, workers) → chained MCP sub-app
lifespan → CSRF middleware exemption → mounted /mcp endpoint → bearer auth →
actor resolution → tool call.

Follows the uvicorn-daemon-thread pattern of test_mcp_http_auth.py: the server
runs in its own thread with its own event loop, tests talk to it over real TCP
sockets, and the DB is seeded via a sync SQLAlchemy engine on the same SQLite
file after the lifespan has run Alembic migrations.

The Google OAuth provider seam (``snore.api.mcp_embed._make_mcp_auth_provider``)
is replaced with a JWTVerifier so no network calls to Google are made.  A
separate test builds the app with the REAL GoogleProvider (dummy credentials —
its metadata routes are static and hit no network) to prove the OAuth discovery
routes are served at the root and not swallowed by the SPA fallback.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from collections.abc import Generator
from datetime import date, datetime, timedelta

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

from snore.database.models import AuthIdentity, Day, Device, Profile, Session, User

# ---------------------------------------------------------------------------
# Override the integration conftest's autouse DB cleanup between tests — the
# module-scoped server owns the global DB engine (see test_mcp_http_auth.py).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_database_state() -> None:
    """Override integration conftest: module-scoped server manages DB lifecycle."""
    return


_RUN_ID = uuid.uuid4().hex[:8]
_SUB_A = f"sub-embedded-a-{_RUN_ID}"

_TEST_AUDIENCE = "https://mcp.snore.test"

# Multiuser env for the embedded app.  The MCP base URL only feeds OAuth
# metadata (issuer) and config validation — it need not match the ephemeral
# port uvicorn actually binds.
_ENV = {
    "SNORE_AUTH_MODE": "multiuser",
    "SNORE_SESSION_SECRET": "embedded-mcp-test-secret-at-least-32-chars",
    "SNORE_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
    "SNORE_MCP_BASE_URL": "http://127.0.0.1:8000",
    "GOOGLE_CLIENT_ID": "dummy-client-id",
    "GOOGLE_CLIENT_SECRET": "dummy-client-secret",
}


# ---------------------------------------------------------------------------
# Sync DB seeding — called after the server has initialized the schema
# ---------------------------------------------------------------------------


def _seed_sync(db_path: str) -> None:
    """Insert one user + profile + google identity + device + day/session."""
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    factory: sessionmaker[OrmSession] = sessionmaker(bind=engine)
    db = factory()
    try:
        user = User(
            canonical_email=f"emb_{uuid.uuid4().hex[:6]}@test.example", role="member"
        )
        db.add(user)
        db.flush()
        profile = Profile(user_id=user.id, name="Embedded Profile")
        db.add(profile)
        db.flush()
        db.add(AuthIdentity(user_id=user.id, provider="google", subject=_SUB_A))
        device = Device(
            profile_id=profile.id,
            manufacturer="MfrA",
            model="Mdl",
            serial_number=f"SN{uuid.uuid4().hex[:8]}",
        )
        db.add(device)
        db.flush()
        day = Day(device_id=device.id, date=date(2024, 1, 10), total_therapy_hours=7.0)
        db.add(day)
        db.flush()
        db.add(
            Session(
                device_id=device.id,
                day_id=day.id,
                device_session_id=f"s{uuid.uuid4().hex[:8]}",
                start_time=datetime(2024, 1, 10, 22, 0),
                end_time=datetime(2024, 1, 10, 22, 0) + timedelta(hours=7),
                duration_seconds=7 * 3600,
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
# Module-scoped env, key pair, verifier, and embedded-server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _env() -> Generator[None]:
    """Set the multiuser + MCP env vars for the module; restore on teardown."""
    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="module")
def _key_pair() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture(scope="module")
def _verifier(_key_pair: RSAKeyPair) -> JWTVerifier:
    return JWTVerifier(public_key=_key_pair.public_key, audience=_TEST_AUDIENCE)


@pytest.fixture(scope="module")
def _server_url(
    tmp_path_factory: pytest.TempPathFactory,
    _env: None,
    _verifier: JWTVerifier,
) -> Generator[str]:
    """Start uvicorn serving create_app() in a daemon thread; yield the base URL.

    Patches (restored on teardown):
    - ``_make_mcp_auth_provider`` → the module's JWTVerifier (no Google calls).
    - ``_startup_ensure_demo_data`` → no-op; the real one imports ~64 MB of
      bundled EDF fixtures on first multiuser boot, which has no bearing on
      these tests and would dominate their runtime.
    """
    import snore.api.app as app_module
    import snore.api.mcp_embed as mcp_embed

    from snore.api.config import reset_config

    db_path = str(tmp_path_factory.mktemp("mcp_embedded") / "test_embedded.db")
    os.environ["SNORE_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"

    async def _no_demo_data(app: object) -> None:
        return None

    orig_demo = app_module._startup_ensure_demo_data
    orig_provider = mcp_embed._make_mcp_auth_provider
    app_module._startup_ensure_demo_data = _no_demo_data
    mcp_embed._make_mcp_auth_provider = lambda cfg: _verifier

    try:
        # create_app() reads the global config — force a reload from this
        # module's env in case an earlier test left a cached config behind.
        reset_config()
        app = app_module.create_app()

        # port=0: the OS assigns an ephemeral port (see test_mcp_http_auth.py).
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, lifespan="on", log_level="error"
        )
        uv = uvicorn.Server(config)
        thread = threading.Thread(target=uv.run, daemon=True)
        thread.start()

        deadline = time.monotonic() + 30.0
        while not uv.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not uv.started:
            uv.should_exit = True
            thread.join(timeout=5)
            raise RuntimeError("uvicorn server failed to start within 30 s")

        port: int = uv.servers[0].sockets[0].getsockname()[1]

        # Lifespan has run Alembic migrations; seed test rows now.
        _seed_sync(db_path)

        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            uv.should_exit = True
            thread.join(timeout=15)
    finally:
        app_module._startup_ensure_demo_data = orig_demo
        mcp_embed._make_mcp_auth_provider = orig_provider
        os.environ.pop("SNORE_DATABASE_URL", None)


# ---------------------------------------------------------------------------
# Tests — embedded server with JWTVerifier auth
# ---------------------------------------------------------------------------


async def test_mcp_unauthenticated_without_origin_gets_401(_server_url: str) -> None:
    """POST /mcp with no token and NO Origin header → 401 with WWW-Authenticate.

    Proves the CSRF origin-check exemption: without it, the multiuser
    unsafe-method check would reject this request with 403 before it ever
    reached the MCP sub-app's bearer-auth middleware.
    """
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{_server_url}/mcp",
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


async def test_authenticated_tool_call_sees_seeded_data(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """fastmcp Client + bearer JWT → initialize + get_data_overview succeed.

    Proves the chained MCP lifespan (session-manager task group), the shared
    FastAPI-owned engine, and per-request ActorRuntime resolution all work
    inside the embedded app.
    """
    token = _key_pair.create_token(
        subject=_SUB_A, expires_in_seconds=3600, audience=_TEST_AUDIENCE
    )
    transport = StreamableHttpTransport(
        url=f"{_server_url}/mcp", auth=BearerAuth(token)
    )
    async with Client(transport=transport) as client:
        result = await client.call_tool("get_data_overview")

    assert result.structured_content is not None
    mfrs = {d["manufacturer"] for d in result.structured_content["devices"]}
    assert mfrs == {"MfrA"}


async def test_cookie_auth_api_route_still_csrf_protected(_server_url: str) -> None:
    """POST to a cookie-auth API route without Origin → 403 (CSRF regression guard)."""
    async with httpx.AsyncClient() as http:
        resp = await http.post(f"{_server_url}/api/v1/profiles/", json={"name": "x"})
    assert resp.status_code == 403


async def test_health_endpoint_serves_alongside_mcp(_server_url: str) -> None:
    """GET /health → 200 JSON — the REST API is unaffected by the /mcp mount."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(f"{_server_url}/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Test — real GoogleProvider: OAuth discovery metadata routes
# ---------------------------------------------------------------------------


async def test_oauth_metadata_routes_with_real_google_provider(
    _env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REAL GoogleProvider's discovery routes are served at the app root.

    Dummy credentials: the metadata routes are static JSON and hit no network.
    Requests go through the full FastAPI stack in-process (ASGITransport, no
    lifespan — the metadata handlers touch neither the DB nor the MCP session
    manager), proving the routes resolve inside the mounted sub-app and are
    returned as JSON, not rewritten to the SPA shell.
    """
    import snore.api.app as app_module
    import snore.api.mcp_embed as mcp_embed

    from snore.api.config import reset_config
    from snore.mcp.auth import make_auth_provider

    # Force the real provider — the module server fixture may have patched the
    # seam to a JWTVerifier (which serves no OAuth metadata routes).
    def _real_provider(cfg: object) -> object:
        return make_auth_provider(
            base_url=_ENV["SNORE_MCP_BASE_URL"],
            google_client_id=_ENV["GOOGLE_CLIENT_ID"],
            google_client_secret=_ENV["GOOGLE_CLIENT_SECRET"],
        )

    monkeypatch.setattr(mcp_embed, "_make_mcp_auth_provider", _real_provider)

    reset_config()
    app = app_module.create_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=_ENV["SNORE_MCP_BASE_URL"]
    ) as http:
        resp = await http.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        metadata = resp.json()
        assert metadata["issuer"].rstrip("/") == _ENV["SNORE_MCP_BASE_URL"]

        resp = await http.get("/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
