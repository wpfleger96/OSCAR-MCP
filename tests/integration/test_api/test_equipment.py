import uuid

from datetime import date, timedelta

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as DbSession

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database.models import MaskLogEntry, Profile, User
from tests.helpers.api_client import make_test_client

MASKS_URL = "/api/v1/equipment/masks"


def _create_entry_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "brand": "ResMed",
        "model": "AirFit P10",
        "style": "pillows",
        "start_date": "2025-06-01",
    }
    payload.update(overrides)
    return payload


def _create_foreign_profile(db_session: DbSession) -> Profile:
    """Seed a second user + profile (NOT the api_client actor's profile)."""
    user = User(
        canonical_email=f"other_{uuid.uuid4().hex[:8]}@example.com",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    profile = Profile(user_id=user.id, name="Other Profile")
    db_session.add(profile)
    db_session.flush()
    return profile


class TestMaskLogCrud:
    def test_list_empty(self, api_client, test_profile):
        response = api_client.get(MASKS_URL)
        assert response.status_code == 200
        assert response.json() == []

    def test_crud_happy_path(self, api_client, test_profile):
        response = api_client.post(MASKS_URL, json=_create_entry_payload())
        assert response.status_code == 201
        created = response.json()
        assert created["brand"] == "ResMed"
        assert created["model"] == "AirFit P10"
        assert created["style"] == "pillows"
        assert created["start_date"] == "2025-06-01"
        assert created["size"] is None
        assert created["notes"] is None
        entry_id = created["id"]

        response = api_client.get(MASKS_URL)
        assert response.status_code == 200
        assert [e["id"] for e in response.json()] == [entry_id]

        response = api_client.patch(
            f"{MASKS_URL}/{entry_id}",
            json={"model": "AirFit N30i", "style": "nasal", "notes": "less leak"},
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["model"] == "AirFit N30i"
        assert updated["style"] == "nasal"
        assert updated["notes"] == "less leak"
        # Untouched fields are unchanged.
        assert updated["brand"] == "ResMed"
        assert updated["start_date"] == "2025-06-01"

        response = api_client.delete(f"{MASKS_URL}/{entry_id}")
        assert response.status_code == 204

        response = api_client.get(MASKS_URL)
        assert response.json() == []

    def test_list_ordered_by_start_date(self, api_client, test_profile):
        for start_date in ("2025-06-15", "2025-06-01", "2025-06-30"):
            response = api_client.post(
                MASKS_URL, json=_create_entry_payload(start_date=start_date)
            )
            assert response.status_code == 201

        response = api_client.get(MASKS_URL)
        assert response.status_code == 200
        assert [e["start_date"] for e in response.json()] == [
            "2025-06-01",
            "2025-06-15",
            "2025-06-30",
        ]

    def test_patch_clears_nullable_field_with_explicit_null(
        self, api_client, test_profile
    ):
        response = api_client.post(
            MASKS_URL, json=_create_entry_payload(size="M", notes="first mask")
        )
        entry_id = response.json()["id"]

        response = api_client.patch(f"{MASKS_URL}/{entry_id}", json={"notes": None})
        assert response.status_code == 200
        updated = response.json()
        assert updated["notes"] is None
        # Omitted nullable field is unchanged.
        assert updated["size"] == "M"


class TestMaskLogValidation:
    def test_create_invalid_style_returns_422(self, api_client, test_profile):
        response = api_client.post(
            MASKS_URL, json=_create_entry_payload(style="full-face")
        )
        assert response.status_code == 422

    def test_create_empty_brand_returns_422(self, api_client, test_profile):
        response = api_client.post(MASKS_URL, json=_create_entry_payload(brand=""))
        assert response.status_code == 422

    @pytest.mark.parametrize("field", ["brand", "model", "style", "start_date"])
    def test_patch_null_required_field_returns_422(
        self, api_client, test_profile, field
    ):
        response = api_client.post(MASKS_URL, json=_create_entry_payload())
        entry_id = response.json()["id"]

        response = api_client.patch(f"{MASKS_URL}/{entry_id}", json={field: None})
        assert response.status_code == 422

    def test_create_too_long_notes_returns_422(self, api_client, test_profile):
        response = api_client.post(
            MASKS_URL, json=_create_entry_payload(notes="x" * 4001)
        )
        assert response.status_code == 422

    def test_create_empty_size_returns_422(self, api_client, test_profile):
        response = api_client.post(MASKS_URL, json=_create_entry_payload(size=""))
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "start_date",
        ["1999-12-31", (date.today() + timedelta(days=730)).isoformat()],
    )
    def test_create_implausible_start_date_returns_422(
        self, api_client, test_profile, start_date
    ):
        response = api_client.post(
            MASKS_URL, json=_create_entry_payload(start_date=start_date)
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "start_date",
        ["1999-12-31", (date.today() + timedelta(days=730)).isoformat()],
    )
    def test_patch_implausible_start_date_returns_422(
        self, api_client, test_profile, start_date
    ):
        response = api_client.post(MASKS_URL, json=_create_entry_payload())
        entry_id = response.json()["id"]

        response = api_client.patch(
            f"{MASKS_URL}/{entry_id}", json={"start_date": start_date}
        )
        assert response.status_code == 422


class TestMaskLogNotFound:
    def test_patch_missing_id_returns_404(self, api_client, test_profile):
        response = api_client.patch(f"{MASKS_URL}/9999", json={"brand": "ResMed"})
        assert response.status_code == 404

    def test_delete_missing_id_returns_404(self, api_client, test_profile):
        response = api_client.delete(f"{MASKS_URL}/9999")
        assert response.status_code == 404

    def test_foreign_profile_entry_returns_404(
        self, api_client, db_session, test_profile
    ):
        """Another profile's entry id → 404, and it is invisible to list."""
        foreign_profile = _create_foreign_profile(db_session)
        foreign_entry = MaskLogEntry(
            profile_id=foreign_profile.id,
            brand="Philips",
            model="DreamWear",
            style="full_face",
            start_date=date(2025, 5, 1),
        )
        db_session.add(foreign_entry)
        db_session.flush()

        response = api_client.patch(
            f"{MASKS_URL}/{foreign_entry.id}", json={"brand": "ResMed"}
        )
        assert response.status_code == 404

        response = api_client.delete(f"{MASKS_URL}/{foreign_entry.id}")
        assert response.status_code == 404

        response = api_client.get(MASKS_URL)
        assert response.status_code == 200
        assert response.json() == []


class TestMaskLogDemoEnforcement:
    """Demo (read-only) actors get 403 from RequireWritable on all mutations."""

    def _demo_client(
        self, async_db_session: AsyncSession, db_session: DbSession
    ) -> TestClient:
        demo_user = User(
            canonical_email=f"demo_{uuid.uuid4().hex[:8]}@example.com",
            role="demo",
        )
        db_session.add(demo_user)
        db_session.flush()
        profile = Profile(user_id=demo_user.id, name="Demo")
        db_session.add(profile)
        db_session.flush()
        actor = ActorContext(
            user_id=demo_user.id,
            profile_id=profile.id,
            role=Role.DEMO,
            mode=AuthMode.MULTIUSER,
        )
        return make_test_client(async_db_session, actor=actor)

    def test_demo_post_returns_403(self, async_db_session, db_session):
        client = self._demo_client(async_db_session, db_session)
        response = client.post(MASKS_URL, json=_create_entry_payload())
        assert response.status_code == 403

    def test_demo_patch_returns_403(self, async_db_session, db_session):
        client = self._demo_client(async_db_session, db_session)
        response = client.patch(f"{MASKS_URL}/1", json={"brand": "ResMed"})
        assert response.status_code == 403

    def test_demo_delete_returns_403(self, async_db_session, db_session):
        client = self._demo_client(async_db_session, db_session)
        response = client.delete(f"{MASKS_URL}/1")
        assert response.status_code == 403

    def test_demo_get_list_allowed(self, async_db_session, db_session):
        client = self._demo_client(async_db_session, db_session)
        response = client.get(MASKS_URL)
        assert response.status_code == 200
        assert response.json() == []
