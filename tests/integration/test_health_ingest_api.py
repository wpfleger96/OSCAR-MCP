"""Integration tests for POST /api/v1/health/ingest.

These tests exercise the full stack: token auth, body-size cap, lockout,
idempotency, and the import pipeline.  They use a real initialized database
(not the TestClient's session override) because ``HealthImportService``
manages its own DB connections via ``session_scope()``.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from pathlib import Path
from typing import Any

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from snore.database.models import HealthNightlySummary, HealthSample, Profile, User
from snore.database.session import init_database, session_scope
from snore.services.health_token_service import HealthTokenService

INGEST_URL = "/api/v1/health/ingest"
HAE_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "health_data" / "hae_payload.json"
)


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------


async def _create_profile() -> int:
    """Create a User + Profile and return the profile_id."""
    async with session_scope() as db:
        user = User(
            canonical_email=f"ingest_{uuid.uuid4().hex[:8]}@test.local",
            role="member",
        )
        db.add(user)
        await db.flush()
        profile = Profile(user_id=user.id, name="Ingest Test Profile")
        db.add(profile)
        await db.flush()
        return profile.id


async def _create_token(profile_id: int, label: str | None = None) -> str:
    """Create an ingest token for *profile_id*; returns the plaintext token."""
    async with session_scope() as db:
        plaintext, _ = await HealthTokenService(db).create_token(profile_id, label)
    return plaintext


async def _revoke_token(token_id: int, profile_id: int) -> None:
    async with session_scope() as db:
        await HealthTokenService(db).revoke(token_id, profile_id)


async def _sample_count(profile_id: int) -> int:
    async with session_scope() as db:
        result = await db.execute(
            select(func.count())
            .select_from(HealthSample)
            .where(HealthSample.profile_id == profile_id)
        )
        return result.scalar() or 0


async def _summary_count(profile_id: int) -> int:
    async with session_scope() as db:
        result = await db.execute(
            select(func.count())
            .select_from(HealthNightlySummary)
            .where(HealthNightlySummary.profile_id == profile_id)
        )
        return result.scalar() or 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ingest_client(temp_db):
    """TestClient backed by a real initialized DB — no get_db override.

    HealthImportService uses session_scope() internally, so both the route's
    token-verification session and the import pipeline share the same
    temp_db-backed engine.
    """
    asyncio.run(init_database(str(temp_db)))

    from snore.api.app import create_app

    app = create_app()
    # No 'with' — skips lifespan; DB already initialized above.
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def hae_payload() -> dict:
    return json.loads(HAE_FIXTURE.read_bytes())


@pytest.fixture
def profile_id(ingest_client: Any) -> int:
    """Create a profile in the ingest_client's DB."""
    return asyncio.run(_create_profile())


@pytest.fixture
def token(profile_id: int) -> str:
    """Create a valid ingest token for profile_id."""
    return asyncio.run(_create_token(profile_id))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_valid_token_inserts_samples_and_recomputes_summaries(
        self, ingest_client, profile_id, token, hae_payload
    ):
        """Valid token + real HAE payload → 200, samples in DB, summaries recomputed."""
        response = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": token},
        )
        assert response.status_code == 200

        body = response.json()
        assert body["inserted"] > 0
        assert body["skipped"] == 0
        assert body["dry_run"] is False

        # Samples written to DB.
        count = asyncio.run(_sample_count(profile_id))
        assert count == body["inserted"]

        # At least one nightly summary recomputed.
        assert body["nights_recomputed"] > 0
        summary_count = asyncio.run(_summary_count(profile_id))
        assert summary_count >= body["nights_recomputed"]

    def test_second_push_is_idempotent(
        self, ingest_client, profile_id, token, hae_payload
    ):
        """Second identical push → 200 with inserted == 0 (all skipped)."""
        ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": token},
        )

        response = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": token},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["inserted"] == 0
        assert body["skipped"] > 0


# ---------------------------------------------------------------------------
# Auth failures
# ---------------------------------------------------------------------------


class TestAuthFailures:
    def test_missing_header_returns_401(self, ingest_client, hae_payload):
        """No X-SNORE-Ingest-Token header → 401."""
        response = ingest_client.post(INGEST_URL, json=hae_payload)
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"

    def test_garbage_token_returns_401(self, ingest_client, hae_payload):
        """Random string token → 401, same opaque detail."""
        response = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": "not-a-real-token"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"

    def test_revoked_token_returns_401(
        self, ingest_client, profile_id, token, hae_payload
    ):
        """Revoked token → 401, same opaque detail as unknown token."""
        # Get the token ID so we can revoke it.
        token_id: int | None = None

        async def _get_token_id() -> int:
            async with session_scope() as db:
                tokens = await HealthTokenService(db).list_tokens(profile_id)
                return tokens[0].id

        token_id = asyncio.run(_get_token_id())
        asyncio.run(_revoke_token(token_id, profile_id))

        response = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": token},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"

    def test_all_auth_failures_return_same_detail(
        self, ingest_client, profile_id, token, hae_payload
    ):
        """Missing, garbage, and revoked tokens all produce the same detail string."""

        async def _revoke() -> None:
            async with session_scope() as db:
                tokens = await HealthTokenService(db).list_tokens(profile_id)
                await HealthTokenService(db).revoke(tokens[0].id, profile_id)

        asyncio.run(_revoke())

        missing = ingest_client.post(INGEST_URL, json=hae_payload)
        garbage = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": "garbage-xyz"},
        )
        revoked = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": token},
        )

        assert missing.status_code == garbage.status_code == revoked.status_code == 401
        details = {
            missing.json()["detail"],
            garbage.json()["detail"],
            revoked.json()["detail"],
        }
        assert len(details) == 1, f"Expected one opaque detail, got: {details}"


# ---------------------------------------------------------------------------
# Body size cap
# ---------------------------------------------------------------------------


class TestBodySizeCap:
    def test_oversize_body_returns_413(self, ingest_client, token):
        """Body exceeding 10 MiB → 413."""
        # Construct a JSON object large enough to exceed the 10 MiB cap.
        oversized = {"data": {"metrics": [], "pad": "x" * (11 * 1024 * 1024)}}
        payload_bytes = json.dumps(oversized).encode()
        assert len(payload_bytes) > 10 * 1024 * 1024

        response = ingest_client.post(
            INGEST_URL,
            content=payload_bytes,
            headers={
                "x-snore-ingest-token": token,
                "content-type": "application/json",
            },
        )
        assert response.status_code == 413

    def test_content_length_cap_returns_413(self, ingest_client, token):
        """Declared Content-Length exceeding 10 MiB → 413 before body read."""
        # Send a tiny body but lie about Content-Length — the route should
        # reject on the header alone and never read the body.
        response = ingest_client.post(
            INGEST_URL,
            content=b'{"data": {}}',
            headers={
                "x-snore-ingest-token": token,
                "content-type": "application/json",
                "content-length": str(11 * 1024 * 1024),
            },
        )
        assert response.status_code == 413


# ---------------------------------------------------------------------------
# Malformed body
# ---------------------------------------------------------------------------


class TestMalformedBody:
    def test_invalid_json_returns_400(self, ingest_client, token):
        """Non-JSON body → 400."""
        response = ingest_client.post(
            INGEST_URL,
            content=b"this is not json",
            headers={
                "x-snore-ingest-token": token,
                "content-type": "application/json",
            },
        )
        assert response.status_code == 400

    def test_json_array_body_returns_400(self, ingest_client, token):
        """JSON array (not object) body → 400."""
        response = ingest_client.post(
            INGEST_URL,
            content=b"[1, 2, 3]",
            headers={
                "x-snore-ingest-token": token,
                "content-type": "application/json",
            },
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Lockout
# ---------------------------------------------------------------------------


class TestLockout:
    def test_repeated_garbage_token_trips_lockout(self, ingest_client, hae_payload):
        """Repeated garbage-token attempts from same client trip the lockout."""
        garbage = "garbage-lockout-test-token-xyz"

        # First attempt: fails with 401 and records failure.
        r1 = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": garbage},
        )
        assert r1.status_code == 401

        # Second attempt with same token immediately after: locked out.
        r2 = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": garbage},
        )
        assert r2.status_code == 429

    def test_lockout_does_not_affect_valid_token(
        self, ingest_client, token, hae_payload
    ):
        """Lockout on a garbage token does not block a valid different token."""
        # Trip lockout on a garbage token.
        garbage = "another-garbage-token-abc123"
        ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": garbage},
        )

        # Valid token from a different hash bucket → still allowed.
        response = ingest_client.post(
            INGEST_URL,
            json=hae_payload,
            headers={"x-snore-ingest-token": token},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# AuthMiddleware exemption (multiuser mode)
# ---------------------------------------------------------------------------


class TestMultiuserAuthExemption:
    def test_valid_token_succeeds_without_session_cookie_in_multiuser(
        self, monkeypatch, temp_db, hae_payload
    ):
        """In multiuser mode, a valid ingest token works without a session cookie.

        Proves the endpoint is exempt from session-cookie auth (AuthMiddleware
        sets actor=None for unauthenticated requests; the route uses token auth
        instead and must not raise 401 for the missing cookie).
        """
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET", "test-secret-at-least-32-chars-long-abcdef"
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

        asyncio.run(init_database(str(temp_db)))

        from snore.api.app import create_app

        app = create_app()
        # no_actor_override — let AuthMiddleware run as in production.
        client = TestClient(app, raise_server_exceptions=True)

        profile_id = asyncio.run(_create_profile())
        token = asyncio.run(_create_token(profile_id))

        # No session cookie; Origin satisfies CSRF for any remaining check.
        response = client.post(
            INGEST_URL,
            json=hae_payload,
            headers={
                "x-snore-ingest-token": token,
                "origin": "http://127.0.0.1:8000",
            },
        )
        assert response.status_code == 200
        assert response.json()["inserted"] > 0
