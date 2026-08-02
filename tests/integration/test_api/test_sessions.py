from datetime import datetime


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
