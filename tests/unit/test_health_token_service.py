"""Unit tests for HealthTokenService."""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import HealthIngestToken, Profile, User
from snore.services.health_token_service import HealthTokenInfo, HealthTokenService


async def _make_profile(session: AsyncSession) -> Profile:
    """Create an isolated User + Profile for cross-profile isolation tests."""
    user = User(
        canonical_email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        role="member",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, name=f"Profile {uuid.uuid4().hex[:6]}")
    session.add(profile)
    await session.flush()
    return profile


class TestCreateAndVerifyRoundtrip:
    """create_token → verify returns the owning profile_id."""

    async def test_verify_returns_correct_profile_id(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        plaintext, _ = await service.create_token(async_test_profile.id)
        assert await service.verify(plaintext) == async_test_profile.id

    async def test_hash_stored_not_plaintext(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        plaintext, info = await service.create_token(
            async_test_profile.id, label="device"
        )
        row = (
            (
                await async_db_session.execute(
                    select(HealthIngestToken).where(HealthIngestToken.id == info.id)
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        # Plaintext must not appear in the stored hash.
        assert row.token_hash != plaintext
        assert plaintext not in row.token_hash
        # SHA-256 hex digest is exactly 64 lowercase hex characters.
        assert len(row.token_hash) == 64
        assert all(c in "0123456789abcdef" for c in row.token_hash)
        # Confirm the exact algorithm matches invite_tokens.hash_invite_token.
        assert row.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()


class TestVerifyFailureCases:
    async def test_unknown_token_returns_none(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        assert await service.verify("not-a-real-token") is None

    async def test_revoked_token_returns_none(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        plaintext, info = await service.create_token(async_test_profile.id)
        await service.revoke(info.id, async_test_profile.id)
        assert await service.verify(plaintext) is None


class TestRevoke:
    async def test_revoke_owned_token_returns_true(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        _, info = await service.create_token(async_test_profile.id)
        assert await service.revoke(info.id, async_test_profile.id) is True

    async def test_revoke_wrong_profile_returns_false(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        _, info = await service.create_token(async_test_profile.id)
        assert await service.revoke(info.id, profile_id=9999) is False

    async def test_double_revoke_returns_false(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        _, info = await service.create_token(async_test_profile.id)
        await service.revoke(info.id, async_test_profile.id)
        assert await service.revoke(info.id, async_test_profile.id) is False

    async def test_revoke_then_verify_fails(self, async_db_session, async_test_profile):
        service = HealthTokenService(async_db_session)
        plaintext, info = await service.create_token(async_test_profile.id)
        await service.revoke(info.id, async_test_profile.id)
        assert await service.verify(plaintext) is None


class TestVerifyUpdatesLastUsedAt:
    async def test_verify_sets_last_used_at(self, async_db_session, async_test_profile):
        service = HealthTokenService(async_db_session)
        plaintext, info = await service.create_token(async_test_profile.id)

        row = (
            (
                await async_db_session.execute(
                    select(HealthIngestToken).where(HealthIngestToken.id == info.id)
                )
            )
            .scalars()
            .first()
        )
        assert row.last_used_at is None

        await service.verify(plaintext)
        await async_db_session.refresh(row)
        assert row.last_used_at is not None


class TestListTokens:
    async def test_empty_profile_returns_empty_list(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        assert await service.list_tokens(async_test_profile.id) == []

    async def test_includes_revoked_tokens(self, async_db_session, async_test_profile):
        service = HealthTokenService(async_db_session)
        _, active_info = await service.create_token(
            async_test_profile.id, label="active"
        )
        _, revoked_info = await service.create_token(
            async_test_profile.id, label="revoked"
        )
        await service.revoke(revoked_info.id, async_test_profile.id)

        tokens = await service.list_tokens(async_test_profile.id)
        assert len(tokens) == 2
        ids = {t.id for t in tokens}
        assert active_info.id in ids
        assert revoked_info.id in ids

    async def test_info_excludes_hash_and_plaintext(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        await service.create_token(async_test_profile.id)
        tokens = await service.list_tokens(async_test_profile.id)
        assert len(tokens) == 1
        token = tokens[0]
        assert isinstance(token, HealthTokenInfo)
        assert not hasattr(token, "token_hash")
        assert not hasattr(token, "plaintext")

    async def test_ordered_by_created_at(self, async_db_session, async_test_profile):
        service = HealthTokenService(async_db_session)
        _, first = await service.create_token(async_test_profile.id, label="first")
        _, second = await service.create_token(async_test_profile.id, label="second")
        _, third = await service.create_token(async_test_profile.id, label="third")

        tokens = await service.list_tokens(async_test_profile.id)
        assert [t.id for t in tokens] == [first.id, second.id, third.id]


class TestCrossProfileIsolation:
    """Tokens must not leak across profile boundaries."""

    async def test_verify_maps_token_to_its_own_profile(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        other_profile = await _make_profile(async_db_session)

        pt_a, _ = await service.create_token(async_test_profile.id, label="a")
        pt_b, _ = await service.create_token(other_profile.id, label="b")

        assert await service.verify(pt_a) == async_test_profile.id
        assert await service.verify(pt_b) == other_profile.id

    async def test_list_tokens_scoped_to_profile(
        self, async_db_session, async_test_profile
    ):
        service = HealthTokenService(async_db_session)
        other_profile = await _make_profile(async_db_session)

        await service.create_token(async_test_profile.id, label="mine")
        await service.create_token(other_profile.id, label="theirs")

        mine = await service.list_tokens(async_test_profile.id)
        theirs = await service.list_tokens(other_profile.id)

        assert len(mine) == 1
        assert mine[0].label == "mine"
        assert len(theirs) == 1
        assert theirs[0].label == "theirs"
