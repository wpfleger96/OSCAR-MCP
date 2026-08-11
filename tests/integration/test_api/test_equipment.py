import uuid

from datetime import date, timedelta

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as DbSession

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database.models import Device, MaskLogEntry, Profile, User
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
    def test_patch_null_clears_field_returns_200(self, api_client, test_profile, field):
        response = api_client.post(MASKS_URL, json=_create_entry_payload())
        entry_id = response.json()["id"]

        response = api_client.patch(f"{MASKS_URL}/{entry_id}", json={field: None})
        assert response.status_code == 200
        assert response.json()[field] is None

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


EPOCHS_URL = "/api/v1/equipment/masks/epochs"


def _create_device(
    db_session: DbSession,
    profile_id: int,
    manufacturer: str = "ResMed",
    model: str = "AirSense 10",
) -> Device:
    device = Device(
        profile_id=profile_id,
        manufacturer=manufacturer,
        model=model,
        serial_number=f"SN_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(device)
    db_session.flush()
    return device


def _create_day_with_settings(
    db_session: DbSession,
    device: Device,
    day_date: date,
    settings: dict[str, str] | None = None,
    enabled: bool = True,
) -> None:
    """Seed a Day + Session (+ optional Settings) for the epochs endpoint tests."""
    from datetime import datetime  # noqa: PLC0415

    from snore.database.models import Day as DayModel  # noqa: PLC0415
    from snore.database.models import Session as SessionModel  # noqa: PLC0415
    from snore.database.models import Setting as SettingModel  # noqa: PLC0415

    day = DayModel(
        device_id=device.id,
        date=day_date,
        session_count=1,
        total_therapy_hours=8.0,
    )
    db_session.add(day)
    db_session.flush()

    sess = SessionModel(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"eq_{device.id}_{day_date.isoformat()}_{uuid.uuid4().hex[:4]}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time()) + timedelta(hours=8),
        duration_seconds=8 * 3600,
        enabled=enabled,
    )
    db_session.add(sess)
    db_session.flush()

    if settings:
        for key, value in settings.items():
            db_session.add(SettingModel(session_id=sess.id, key=key, value=value))
        db_session.flush()


class TestMaskLogOptionalFields:
    """POST/PATCH/GET behavior once brand/model/style/start_date become nullable."""

    def test_empty_body_post_returns_201_all_null(self, api_client, test_profile):
        response = api_client.post(MASKS_URL, json={})
        assert response.status_code == 201
        body = response.json()
        assert body["brand"] is None
        assert body["model"] is None
        assert body["style"] is None
        assert body["start_date"] is None
        assert body["size"] is None

    def test_partial_post_style_and_date_only_returns_201(
        self, api_client, test_profile
    ):
        response = api_client.post(
            MASKS_URL,
            json={"style": "pillows", "start_date": "2025-06-01"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["style"] == "pillows"
        assert body["start_date"] == "2025-06-01"
        assert body["brand"] is None
        assert body["model"] is None

    @pytest.mark.parametrize("field", ["brand", "model", "style", "start_date"])
    def test_patch_null_clears_identity_field(self, api_client, test_profile, field):
        response = api_client.post(MASKS_URL, json=_create_entry_payload())
        assert response.status_code == 201
        entry_id = response.json()["id"]

        response = api_client.patch(f"{MASKS_URL}/{entry_id}", json={field: None})
        assert response.status_code == 200
        assert response.json()[field] is None

    def test_list_orders_null_start_date_entries_last(self, api_client, test_profile):
        # Create two dated entries and one with no start_date.
        for start_date in ("2025-06-15", "2025-06-01"):
            resp = api_client.post(
                MASKS_URL, json=_create_entry_payload(start_date=start_date)
            )
            assert resp.status_code == 201
        # Create and then clear the start_date to produce a null-date entry.
        resp = api_client.post(MASKS_URL, json=_create_entry_payload())
        null_entry_id = resp.json()["id"]
        api_client.patch(f"{MASKS_URL}/{null_entry_id}", json={"start_date": None})

        response = api_client.get(MASKS_URL)
        assert response.status_code == 200
        entries = response.json()
        assert len(entries) == 3
        assert entries[0]["start_date"] == "2025-06-01"
        assert entries[1]["start_date"] == "2025-06-15"
        assert entries[2]["start_date"] is None

    def test_create_empty_string_brand_still_returns_422(
        self, api_client, test_profile
    ):
        response = api_client.post(MASKS_URL, json={"brand": ""})
        assert response.status_code == 422


class TestMaskEpochsEndpoint:
    """GET /api/v1/equipment/masks/epochs — device mask-type epoch timeline."""

    def test_no_data_returns_empty_list(self, api_client, test_profile):
        response = api_client.get(EPOCHS_URL)
        assert response.status_code == 200
        assert response.json() == []

    def test_two_mask_types_two_epochs(self, api_client, db_session, test_profile):
        """Two distinct mask_type values produce two separate epochs."""
        device = _create_device(db_session, test_profile.id)
        base = date(2025, 6, 1)
        rx = {"mode": "CPAP", "pressure_fixed": "8.0"}

        # Epoch 1: Pillows on days 0–2
        for i in range(3):
            _create_day_with_settings(
                db_session,
                device,
                base + timedelta(days=i),
                settings={**rx, "mask_type": "Pillows"},
            )
        # Epoch 2: Nasal on days 3–4
        for i in range(3, 5):
            _create_day_with_settings(
                db_session,
                device,
                base + timedelta(days=i),
                settings={**rx, "mask_type": "Nasal"},
            )

        response = api_client.get(EPOCHS_URL)
        assert response.status_code == 200
        epochs = response.json()

        assert len(epochs) == 2
        pillows_epoch = epochs[0]
        nasal_epoch = epochs[1]

        assert pillows_epoch["mask_type"] == "Pillows"
        assert pillows_epoch["style"] == "pillows"
        assert pillows_epoch["start_date"] == str(base)
        assert pillows_epoch["end_date"] == str(base + timedelta(days=2))
        assert pillows_epoch["days_count"] == 3
        assert "AirSense 10" in pillows_epoch["device_name"]

        assert nasal_epoch["mask_type"] == "Nasal"
        assert nasal_epoch["style"] == "nasal"
        assert nasal_epoch["start_date"] == str(base + timedelta(days=3))
        assert nasal_epoch["end_date"] == str(base + timedelta(days=4))
        assert nasal_epoch["days_count"] == 2

    def test_unknown_mask_type_returns_null_style(
        self, api_client, db_session, test_profile
    ):
        """A mask_type value not in the normalization map yields style=null."""
        device = _create_device(db_session, test_profile.id)
        base = date(2025, 7, 1)
        for i in range(2):
            _create_day_with_settings(
                db_session,
                device,
                base + timedelta(days=i),
                settings={"mask_type": "Unknown"},
            )

        response = api_client.get(EPOCHS_URL)
        assert response.status_code == 200
        epochs = response.json()

        assert len(epochs) == 1
        assert epochs[0]["mask_type"] == "Unknown"
        assert epochs[0]["style"] is None

    def test_gap_day_bridges_same_mask_type_into_one_epoch(
        self, api_client, db_session, test_profile
    ):
        """A settingless gap day between two same-mask_type days forms one epoch."""
        device = _create_device(db_session, test_profile.id)
        base = date(2025, 8, 1)

        # Day 0: Nasal
        _create_day_with_settings(
            db_session,
            device,
            base,
            settings={"mask_type": "Nasal"},
        )
        # Day 1: no mask_type setting (only other settings, no mask_type key)
        _create_day_with_settings(
            db_session,
            device,
            base + timedelta(days=1),
            settings={"mode": "CPAP"},
        )
        # Day 2: Nasal again — same as day 0, should bridge
        _create_day_with_settings(
            db_session,
            device,
            base + timedelta(days=2),
            settings={"mask_type": "Nasal"},
        )

        response = api_client.get(EPOCHS_URL)
        assert response.status_code == 200
        epochs = response.json()

        assert len(epochs) == 1
        assert epochs[0]["mask_type"] == "Nasal"
        assert epochs[0]["start_date"] == str(base)
        assert epochs[0]["end_date"] == str(base + timedelta(days=2))
        assert epochs[0]["days_count"] == 2  # only the days WITH mask_type

    def test_full_face_mask_type_normalized_to_full_face(
        self, api_client, db_session, test_profile
    ):
        device = _create_device(db_session, test_profile.id)
        _create_day_with_settings(
            db_session,
            device,
            date(2025, 9, 1),
            settings={"mask_type": "Full Face"},
        )

        response = api_client.get(EPOCHS_URL)
        assert response.status_code == 200
        epochs = response.json()
        assert len(epochs) == 1
        assert epochs[0]["style"] == "full_face"

    def test_profile_isolation_other_profile_invisible(
        self, api_client, db_session, test_profile
    ):
        """Settings seeded under a foreign profile are not returned."""
        foreign_profile = _create_foreign_profile(db_session)
        foreign_device = _create_device(db_session, foreign_profile.id)
        _create_day_with_settings(
            db_session,
            foreign_device,
            date(2025, 6, 1),
            settings={"mask_type": "Pillows"},
        )

        response = api_client.get(EPOCHS_URL)
        assert response.status_code == 200
        assert response.json() == []

    def test_demo_role_can_read_epochs(self, async_db_session, db_session):
        """Demo (read-only) actors are allowed to GET the epochs endpoint."""
        demo_user = User(
            canonical_email=f"demo_{uuid.uuid4().hex[:8]}@example.com",
            role="demo",
        )
        db_session.add(demo_user)
        db_session.flush()
        demo_profile = Profile(user_id=demo_user.id, name="Demo")
        db_session.add(demo_profile)
        db_session.flush()
        actor = ActorContext(
            user_id=demo_user.id,
            profile_id=demo_profile.id,
            role=Role.DEMO,
            mode=AuthMode.MULTIUSER,
        )
        client = make_test_client(async_db_session, actor=actor)

        response = client.get(EPOCHS_URL)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
