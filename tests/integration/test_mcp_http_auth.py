"""End-to-end integration tests for SNORE MCP HTTP OAuth auth path.

No network calls to Google.  Uses fastmcp's RSAKeyPair + JWTVerifier for
in-process JWT signing and verification.  The server runs on an ephemeral
localhost port via uvicorn in a daemon thread, exercising the full:
    HTTP auth middleware → token validation → actor resolution → tool call chain.

Transport choice: uvicorn in a daemon thread (own event loop).
- uvicorn runs the ASGI lifespan naturally (DB init, cleanup).
- The thread's event loop is independent of pytest's asyncio event loop, so
  uvicorn keeps processing requests while async tests run.  They communicate
  through real OS-level TCP sockets — no event-loop sharing required.
- ASGITransport (in-process) is NOT used: it does not run the ASGI lifespan
  and cannot share a socket-based connection with async MCP clients.

RSAKeyPair / JWTVerifier API (fastmcp.server.auth.providers.jwt):
- RSAKeyPair.generate()  → generates a 2048-bit RSA key pair.
- key_pair.create_token(subject=..., expires_in_seconds=...) → signed JWT.
- JWTVerifier(public_key=key_pair.public_key) → verifier using the RSA
  public key; passed to make_server(auth=...) instead of GoogleProvider.

Module-scoped server + DB for speed: the server starts once and users are
seeded once; all tests in the module share the running server.

DB lifecycle:
  1. make_server() lifespan calls init_database_from_url → schema + engine.
  2. After the server is ready, _seed_sync() writes test rows via a plain
     sync SQLAlchemy session on the same SQLite file.
  3. Tests make HTTP requests to the running server; uvicorn's engine reads
     the seeded rows from the shared WAL file.
  4. On fixture teardown, uvicorn stops; its lifespan calls cleanup_database().
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
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

from snore.database.models import AuthIdentity, Day, Device, Profile, Session, User
from snore.mcp.server import make_server

# ---------------------------------------------------------------------------
# Override the integration conftest's autouse DB cleanup between tests.
#
# The module-scoped server created by _server_url owns the global DB engine
# for the duration of the module.  The conftest's reset_database_state calls
# cleanup_database() via asyncio.run() between tests, which would dispose the
# global engine mid-flight and break subsequent actor_scope() calls.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_database_state() -> None:
    """Override integration conftest: module-scoped server manages DB lifecycle."""
    return


# ---------------------------------------------------------------------------
# Stable subject values — generated once at module load, used across all tests
# as JWT ``sub`` claims and auth_identities rows.
# ---------------------------------------------------------------------------

_RUN_ID = uuid.uuid4().hex[:8]
_SUB_A = f"sub-user-a-{_RUN_ID}"
_SUB_B = f"sub-user-b-{_RUN_ID}"
_SUB_DISABLED = f"sub-disabled-{_RUN_ID}"
_SUB_TWO_PROFILES = f"sub-two-profiles-{_RUN_ID}"
# _SUB_UNKNOWN is intentionally NOT inserted into auth_identities.
_SUB_UNKNOWN = f"sub-unknown-{_RUN_ID}"

# Audience string used by JWTVerifier and minted into every valid test token.
# Tokens with a different audience are rejected by the auth middleware (401).
_TEST_AUDIENCE = "https://mcp.snore.test"


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------


def _mint_token(
    key_pair: RSAKeyPair,
    sub: str,
    expires_in: int = 3600,
    audience: str | None = _TEST_AUDIENCE,
) -> str:
    return key_pair.create_token(
        subject=sub,
        expires_in_seconds=expires_in,
        audience=audience,
    )


def _mcp_client(url: str, token: str) -> Client:
    """Return a fastmcp Client configured with a bearer token."""
    return Client(transport=StreamableHttpTransport(url=url, auth=BearerAuth(token)))


# ---------------------------------------------------------------------------
# Sync DB seeding — called after the server has initialized the schema
# ---------------------------------------------------------------------------


def _seed_sync(db_path: str) -> None:
    """Insert test rows into the already-initialized SQLite DB.

    Uses a plain synchronous SQLAlchemy session.  The server's lifespan has
    already run Alembic migrations; this function only inserts rows.

    Why sync: the server's async engine runs in uvicorn's daemon-thread event
    loop.  Seeding from the pytest event loop (or from a plain sync engine)
    avoids any cross-loop sharing.  SQLite's WAL mode handles the concurrent
    access transparently.
    """
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    factory: sessionmaker[OrmSession] = sessionmaker(bind=engine)
    db = factory()
    try:
        _add_all_users(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()


def _add_all_users(db: OrmSession) -> None:
    """Add all test users, profiles, auth identities, and sessions to the DB."""

    def _dev(profile_id: int, manufacturer: str) -> Device:
        d = Device(
            profile_id=profile_id,
            manufacturer=manufacturer,
            model="Mdl",
            serial_number=f"SN{uuid.uuid4().hex[:8]}",
        )
        db.add(d)
        db.flush()
        return d

    def _day_session(device: Device, day_date: date, hours: float) -> None:
        day = Day(device_id=device.id, date=day_date, total_therapy_hours=hours)
        db.add(day)
        db.flush()
        db.add(
            Session(
                device_id=device.id,
                day_id=day.id,
                device_session_id=f"s{uuid.uuid4().hex[:8]}",
                start_time=datetime(day_date.year, day_date.month, day_date.day, 22, 0),
                end_time=datetime(day_date.year, day_date.month, day_date.day, 22, 0)
                + timedelta(hours=hours),
                duration_seconds=hours * 3600,
                enabled=True,
            )
        )
        db.flush()

    # ---- User A: single profile, device "MfrA", data on 2024-01-10 ----
    user_a = User(
        canonical_email=f"ua_{uuid.uuid4().hex[:6]}@test.example", role="member"
    )
    db.add(user_a)
    db.flush()
    profile_a = Profile(user_id=user_a.id, name="Profile A")
    db.add(profile_a)
    db.flush()
    db.add(AuthIdentity(user_id=user_a.id, provider="google", subject=_SUB_A))
    dev_a = _dev(profile_a.id, "MfrA")
    _day_session(dev_a, date(2024, 1, 10), 7.0)

    # ---- User B: single profile, device "MfrB", data on 2024-02-15 ----
    user_b = User(
        canonical_email=f"ub_{uuid.uuid4().hex[:6]}@test.example", role="member"
    )
    db.add(user_b)
    db.flush()
    profile_b = Profile(user_id=user_b.id, name="Profile B")
    db.add(profile_b)
    db.flush()
    db.add(AuthIdentity(user_id=user_b.id, provider="google", subject=_SUB_B))
    dev_b = _dev(profile_b.id, "MfrB")
    _day_session(dev_b, date(2024, 2, 15), 8.0)

    # ---- Disabled user: has an auth identity but disabled_at is set ----
    user_dis = User(
        canonical_email=f"ud_{uuid.uuid4().hex[:6]}@test.example",
        role="member",
        disabled_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.add(user_dis)
    db.flush()
    profile_dis = Profile(user_id=user_dis.id, name="Disabled Profile")
    db.add(profile_dis)
    db.flush()
    db.add(AuthIdentity(user_id=user_dis.id, provider="google", subject=_SUB_DISABLED))

    # ---- Two-profile user: default_profile_id = second profile ----
    # Only the second profile has a device + session; the first has none.
    # This proves the second profile is selected (not the first or the fallback).
    user_2p = User(
        canonical_email=f"u2p_{uuid.uuid4().hex[:6]}@test.example", role="member"
    )
    db.add(user_2p)
    db.flush()
    profile_first = Profile(user_id=user_2p.id, name="First Profile")
    db.add(profile_first)
    db.flush()
    profile_second = Profile(user_id=user_2p.id, name="Second Profile")
    db.add(profile_second)
    db.flush()
    user_2p.default_profile_id = profile_second.id
    db.flush()
    db.add(
        AuthIdentity(user_id=user_2p.id, provider="google", subject=_SUB_TWO_PROFILES)
    )
    dev_2p = _dev(profile_second.id, "Mfr2P")
    _day_session(dev_2p, date(2024, 3, 5), 6.0)

    # Note: _SUB_UNKNOWN has no AuthIdentity row — intentionally absent.


# ---------------------------------------------------------------------------
# Module-scoped key pair, verifier, and server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _key_pair() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture(scope="module")
def _verifier(_key_pair: RSAKeyPair) -> JWTVerifier:
    return JWTVerifier(public_key=_key_pair.public_key, audience=_TEST_AUDIENCE)


@pytest.fixture(scope="module")
def _server_url(
    tmp_path_factory: pytest.TempPathFactory,
    _verifier: JWTVerifier,
) -> Generator[str]:
    """Start uvicorn in a daemon thread, seed the DB, yield the MCP URL.

    The server starts first so its lifespan runs Alembic migrations and sets
    up the async engine.  We then seed test data via a sync SQLAlchemy engine
    pointing at the same SQLite file.  TCP sockets decouple the server's event
    loop (daemon thread) from pytest's asyncio event loop.

    Teardown: signals uvicorn to exit and joins the thread.
    """
    db_path = str(tmp_path_factory.mktemp("http_auth") / "test_http_auth.db")

    server = make_server(db_flag=db_path, auth=_verifier)
    # host_origin_protection=False: suppress localhost Host-header validation
    # that would reject test requests originating from 127.0.0.1.
    app = server.http_app(host_origin_protection=False)
    # port=0: the OS assigns an ephemeral port, eliminating the TOCTOU race
    # between _free_port() closing its probe socket and uvicorn binding.
    # The actual port is read from uv.servers after startup completes.
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

    deadline = time.monotonic() + 15.0
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not uv.started:
        uv.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("uvicorn server failed to start within 15 s")

    # Read the OS-assigned port from the started asyncio server.
    port: int = uv.servers[0].sockets[0].getsockname()[1]

    # Server has initialized the schema via Alembic. Seed test users now.
    _seed_sync(db_path)

    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        thread.join(timeout=15)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_request_without_token_rejected_401(_server_url: str) -> None:
    """Raw POST to the MCP endpoint without Authorization → 401 + WWW-Authenticate."""
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            _server_url,
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


async def test_token_with_unknown_sub_gets_sanitized_tool_error(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """Valid JWT, sub not in auth_identities → isError=True; message omits the sub."""
    token = _mint_token(_key_pair, _SUB_UNKNOWN)
    async with _mcp_client(_server_url, token) as client:
        result = await client.call_tool("get_data_overview", raise_on_error=False)

    assert result.is_error
    error_text = " ".join(
        block.text for block in result.content if hasattr(block, "text")
    )
    assert _SUB_UNKNOWN not in error_text


async def test_two_users_see_only_their_own_data(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """Two users each see only their own profile's device in get_data_overview."""
    token_a = _mint_token(_key_pair, _SUB_A)
    token_b = _mint_token(_key_pair, _SUB_B)

    async with _mcp_client(_server_url, token_a) as client:
        result_a = await client.call_tool("get_data_overview")
    async with _mcp_client(_server_url, token_b) as client:
        result_b = await client.call_tool("get_data_overview")

    assert result_a.structured_content is not None
    assert result_b.structured_content is not None

    mfrs_a = {d["manufacturer"] for d in result_a.structured_content["devices"]}
    mfrs_b = {d["manufacturer"] for d in result_b.structured_content["devices"]}

    assert mfrs_a == {"MfrA"}, f"User A saw unexpected devices: {mfrs_a}"
    assert mfrs_b == {"MfrB"}, f"User B saw unexpected devices: {mfrs_b}"
    assert "MfrB" not in mfrs_a
    assert "MfrA" not in mfrs_b


async def test_disabled_user_rejected(_server_url: str, _key_pair: RSAKeyPair) -> None:
    """Disabled account: JWT validated by middleware, tool call errors; message omits sub."""
    token = _mint_token(_key_pair, _SUB_DISABLED)
    async with _mcp_client(_server_url, token) as client:
        result = await client.call_tool("get_data_overview", raise_on_error=False)

    assert result.is_error
    error_text = " ".join(
        block.text for block in result.content if hasattr(block, "text")
    )
    assert _SUB_DISABLED not in error_text


async def test_default_profile_fallback_used_over_first_profile(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """User with two profiles and default_profile_id=second → overview reflects second profile."""
    token = _mint_token(_key_pair, _SUB_TWO_PROFILES)
    async with _mcp_client(_server_url, token) as client:
        result = await client.call_tool("get_data_overview")

    assert result.structured_content is not None
    devices: list[dict[str, Any]] = result.structured_content["devices"]
    # Second profile has Mfr2P; first profile has no device seeded.
    mfrs = {d["manufacturer"] for d in devices}
    assert "Mfr2P" in mfrs, f"Expected Mfr2P in device list, got: {mfrs}"
    # Exactly one device: the one belonging to the second (default) profile.
    assert len(devices) == 1, f"Expected 1 device (second profile only), got: {devices}"


async def test_expired_token_rejected_401(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """Already-expired JWT → HTTP 401 from auth middleware before any MCP dispatch."""
    expired_token = _mint_token(_key_pair, _SUB_A, expires_in=-60)
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            _server_url,
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
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {expired_token}",
            },
        )
    assert resp.status_code == 401


async def test_wrong_audience_token_rejected_401(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """Token with wrong audience → HTTP 401 from auth middleware; no MCP dispatch."""
    wrong_aud_token = _mint_token(
        _key_pair, _SUB_A, audience="https://other.service.example"
    )
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            _server_url,
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
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {wrong_aud_token}",
            },
        )
    assert resp.status_code == 401


async def test_concurrent_requests_do_not_share_actor(
    _server_url: str, _key_pair: RSAKeyPair
) -> None:
    """Two users' requests interleave on the same server without actor bleed-through.

    Both clients are open simultaneously and their tool calls are dispatched via
    asyncio.gather, so the server handles them concurrently.  Each response must
    contain only that user's data markers (MfrA / MfrB), proving that
    per-request actor resolution is fully isolated.
    """
    token_a = _mint_token(_key_pair, _SUB_A)
    token_b = _mint_token(_key_pair, _SUB_B)

    async with _mcp_client(_server_url, token_a) as client_a:
        async with _mcp_client(_server_url, token_b) as client_b:
            result_a, result_b = await asyncio.gather(
                client_a.call_tool("get_data_overview"),
                client_b.call_tool("get_data_overview"),
            )

    assert result_a.structured_content is not None
    assert result_b.structured_content is not None

    mfrs_a = {d["manufacturer"] for d in result_a.structured_content["devices"]}
    mfrs_b = {d["manufacturer"] for d in result_b.structured_content["devices"]}

    assert mfrs_a == {"MfrA"}, f"User A saw unexpected devices under load: {mfrs_a}"
    assert mfrs_b == {"MfrB"}, f"User B saw unexpected devices under load: {mfrs_b}"
    assert "MfrB" not in mfrs_a
    assert "MfrA" not in mfrs_b
