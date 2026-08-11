from datetime import date, datetime


class TestSessionsList:
    def test_list_empty(self, api_client):
        response = api_client.get("/api/v1/sessions/")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_with_sessions(
        self, api_client, db_session, test_device, test_session_factory
    ):
        test_session_factory(test_device.id, start_time=datetime(2024, 1, 1, 22, 0))
        test_session_factory(test_device.id, start_time=datetime(2024, 1, 2, 22, 0))
        response = api_client.get("/api/v1/sessions/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_pagination_limit(
        self, api_client, db_session, test_device, test_session_factory
    ):
        for i in range(5):
            test_session_factory(
                test_device.id, start_time=datetime(2024, 1, i + 1, 22, 0)
            )
        response = api_client.get("/api/v1/sessions/?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["limit"] == 2

    def test_list_pagination_offset(
        self, api_client, db_session, test_device, test_session_factory
    ):
        for i in range(5):
            test_session_factory(
                test_device.id, start_time=datetime(2024, 1, i + 1, 22, 0)
            )
        response = api_client.get("/api/v1/sessions/?limit=2&offset=3")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["offset"] == 3

    def test_list_filter_by_device(
        self, api_client, db_session, test_device, test_profile, test_session_factory
    ):
        from snore.database.models import Device

        other_device = Device(
            profile_id=test_profile.id,
            manufacturer="Other",
            model="Model",
            serial_number="OTHER_001",
        )
        db_session.add(other_device)
        db_session.flush()

        test_session_factory(test_device.id, start_time=datetime(2024, 1, 1, 22, 0))
        test_session_factory(other_device.id, start_time=datetime(2024, 1, 2, 22, 0))

        response = api_client.get(
            f"/api/v1/sessions/?device={test_device.serial_number}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["serial_number"] == test_device.serial_number

    def test_list_include_disabled_false(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        session.enabled = False
        db_session.flush()

        response = api_client.get("/api/v1/sessions/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_list_include_disabled_true(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        session.enabled = False
        db_session.flush()

        response = api_client.get("/api/v1/sessions/?include_disabled=true")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1


class TestGetSession:
    def test_get_session_not_found(self, api_client):
        response = api_client.get("/api/v1/sessions/99999")
        assert response.status_code == 404

    def test_get_session_success(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.get(f"/api/v1/sessions/{session.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session.id
        assert data["device_serial"] == test_device.serial_number
        assert "import_source" in data
        assert "parser_version" in data
        assert "data_quality_notes" in data
        assert data["data_quality_notes"] == []

    def test_get_session_includes_statistics(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id,
            start_time=datetime(2024, 1, 1, 22, 0),
            ahi=3.5,
            usage_hours=7.0,
        )
        response = api_client.get(f"/api/v1/sessions/{session.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["has_statistics"] is True
        assert data["statistics"]["ahi"] == 3.5

    def test_get_session_active_mask_populated(
        self,
        api_client,
        db_session,
        test_device,
        test_profile,
        test_session_factory,
    ):
        """active_mask is populated when a mask entry precedes the session date."""
        from snore.database.models import MaskLogEntry  # noqa: PLC0415

        session = test_session_factory(
            test_device.id, start_time=datetime(2025, 6, 15, 22, 0)
        )

        entry = MaskLogEntry(
            profile_id=test_profile.id,
            brand="ResMed",
            model="AirFit P10",
            style="pillows",
            start_date=date(2025, 6, 1),
        )
        db_session.add(entry)
        db_session.flush()

        response = api_client.get(f"/api/v1/sessions/{session.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["active_mask"] is not None
        assert data["active_mask"]["brand"] == "ResMed"
        assert data["active_mask"]["model"] == "AirFit P10"

    def test_get_session_active_mask_null_when_no_entries(
        self, api_client, db_session, test_device, test_profile, test_session_factory
    ):
        """active_mask is null in the response when there are no mask log entries."""
        session = test_session_factory(
            test_device.id, start_time=datetime(2025, 6, 15, 22, 0)
        )

        response = api_client.get(f"/api/v1/sessions/{session.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["active_mask"] is None


class TestUpdateSession:
    def test_disable_session(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        assert session.enabled is True

        response = api_client.patch(
            f"/api/v1/sessions/{session.id}",
            json={"enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    def test_enable_already_enabled_session(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.patch(
            f"/api/v1/sessions/{session.id}",
            json={"enabled": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

    def test_update_session_not_found(self, api_client):
        response = api_client.patch(
            "/api/v1/sessions/99999",
            json={"enabled": False},
        )
        assert response.status_code == 404


class TestDeleteSessions:
    def test_delete_sessions(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.request(
            "DELETE",
            "/api/v1/sessions/",
            json={"session_ids": [session.id]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 1

    def test_delete_empty_list(self, api_client):
        response = api_client.request(
            "DELETE",
            "/api/v1/sessions/",
            json={"session_ids": []},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 0


class TestDeletePreview:
    def test_delete_preview_not_found(self, api_client):
        response = api_client.get("/api/v1/sessions/99999/delete-preview")
        assert response.status_code == 200
        data = response.json()
        assert data["sessions"] == []

    def test_delete_preview_with_session(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.get(f"/api/v1/sessions/{session.id}/delete-preview")
        assert response.status_code == 200
        data = response.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == session.id
        assert data["event_count"] == 0
        assert data["waveform_count"] == 0


class TestBulkDeletePreview:
    def test_delete_all_true_empty_db(self, api_client):
        response = api_client.post(
            "/api/v1/sessions/delete-preview", json={"delete_all": True}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["sessions"] == []

    def test_session_ids_filter(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.post(
            "/api/v1/sessions/delete-preview",
            json={"session_ids": [session.id]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == session.id


# ---------------------------------------------------------------------------
# Route-level two-profile isolation: DELETE /sessions/ must 404 on foreign IDs
# ---------------------------------------------------------------------------


class TestDeleteSessionsCrossProfileIsolation:
    """Route-level proof that DELETE /api/v1/sessions/ returns 404 when any
    requested ID belongs to a different profile — foreign rows must survive.

    The actor is overridden directly so the test runs at the HTTP boundary,
    exercising the full route→service→DB stack for both the 404 branch and the
    survival assertion.
    """

    def _make_client_as_profile(
        self, async_db_session: object, db_session: object, profile_id: int
    ) -> object:
        """Return a TestClient whose actor is locked to *profile_id*."""
        from fastapi.testclient import TestClient

        from snore.api.app import create_app
        from snore.api.deps import get_actor, get_db
        from snore.auth.actor import ActorContext, AuthMode, Role

        actor = ActorContext(
            user_id=1,
            profile_id=profile_id,
            role=Role.ADMIN,
            mode=AuthMode.LOCAL,
        )

        app = create_app()

        async def override_get_db():
            async with async_db_session.begin():
                yield async_db_session

        async def override_get_actor():
            return actor

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_actor] = override_get_actor
        client = TestClient(app, raise_server_exceptions=True)
        return client

    def test_delete_foreign_session_ids_returns_404(self, async_db_session, db_session):
        """Profile A requesting deletion of profile B's session IDs -> 404.

        The foreign session must survive -- no rows are deleted.
        """
        from snore.database.models import Device, Profile, Session, User

        # --- Seed two profiles ---
        user_a = User(canonical_email="prof_iso_a@test", role="admin")
        user_b = User(canonical_email="prof_iso_b@test", role="admin")
        db_session.add(user_a)
        db_session.add(user_b)
        db_session.flush()

        profile_a = Profile(user_id=user_a.id, name="A")
        profile_b = Profile(user_id=user_b.id, name="B")
        db_session.add(profile_a)
        db_session.add(profile_b)
        db_session.flush()

        dev_b = Device(
            profile_id=profile_b.id,
            manufacturer="Mfr",
            model="Model",
            serial_number="ISO_DEV_B",
        )
        db_session.add(dev_b)
        db_session.flush()

        foreign_session = Session(
            device_id=dev_b.id,
            device_session_id="iso_foreign_session",
            start_time=datetime(2025, 1, 1, 22, 0),
            end_time=datetime(2025, 1, 2, 6, 0),
            duration_seconds=28800.0,
        )
        db_session.add(foreign_session)
        db_session.flush()
        foreign_id = foreign_session.id

        # --- Make request as profile A with profile B's session ID ---
        client = self._make_client_as_profile(
            async_db_session, db_session, profile_a.id
        )
        response = client.request(
            "DELETE",
            "/api/v1/sessions/",
            json={"session_ids": [foreign_id]},
        )

        assert response.status_code == 404, (
            f"Expected 404 for foreign session ID; got {response.status_code}: "
            f"{response.text}"
        )

        # Foreign session must still exist.
        surviving = db_session.get(Session, foreign_id)
        assert surviving is not None, (
            "Foreign session must survive a cross-profile DELETE attempt"
        )

    def test_delete_mixed_own_and_foreign_returns_404_nothing_deleted(
        self, async_db_session, db_session
    ):
        """Mixed list of own + foreign IDs -> 404; neither own nor foreign is deleted."""
        from snore.database.models import Device, Profile, Session, User

        user_a = User(canonical_email="mix_iso_a@test", role="admin")
        user_b = User(canonical_email="mix_iso_b@test", role="admin")
        db_session.add(user_a)
        db_session.add(user_b)
        db_session.flush()

        profile_a = Profile(user_id=user_a.id, name="A")
        profile_b = Profile(user_id=user_b.id, name="B")
        db_session.add(profile_a)
        db_session.add(profile_b)
        db_session.flush()

        dev_a = Device(
            profile_id=profile_a.id,
            manufacturer="Mfr",
            model="Model",
            serial_number="MIX_DEV_A",
        )
        dev_b = Device(
            profile_id=profile_b.id,
            manufacturer="Mfr",
            model="Model",
            serial_number="MIX_DEV_B",
        )
        db_session.add(dev_a)
        db_session.add(dev_b)
        db_session.flush()

        own_session = Session(
            device_id=dev_a.id,
            device_session_id="mix_own_session",
            start_time=datetime(2025, 2, 1, 22, 0),
            end_time=datetime(2025, 2, 2, 6, 0),
            duration_seconds=28800.0,
        )
        foreign_session = Session(
            device_id=dev_b.id,
            device_session_id="mix_foreign_session",
            start_time=datetime(2025, 2, 1, 22, 0),
            end_time=datetime(2025, 2, 2, 6, 0),
            duration_seconds=28800.0,
        )
        db_session.add(own_session)
        db_session.add(foreign_session)
        db_session.flush()
        own_id = own_session.id
        foreign_id = foreign_session.id

        client = self._make_client_as_profile(
            async_db_session, db_session, profile_a.id
        )
        response = client.request(
            "DELETE",
            "/api/v1/sessions/",
            json={"session_ids": [own_id, foreign_id]},
        )

        assert response.status_code == 404, (
            f"Expected 404 for mixed list; got {response.status_code}: {response.text}"
        )

        # Both rows must survive -- no partial delete on 404.
        assert db_session.get(Session, own_id) is not None, "Own session must survive"
        assert db_session.get(Session, foreign_id) is not None, (
            "Foreign session must survive"
        )
