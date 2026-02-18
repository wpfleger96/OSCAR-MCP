from datetime import datetime


class TestListWaveforms:
    def test_list_waveforms_empty(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.get(f"/api/v1/sessions/{session.id}/waveforms")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_waveforms_not_found_session(self, api_client):
        response = api_client.get("/api/v1/sessions/99999/waveforms")
        assert response.status_code == 200
        data = response.json()
        assert data == []


class TestGetWaveform:
    def test_get_waveform_not_found(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.get(f"/api/v1/sessions/{session.id}/waveforms/flow")
        assert response.status_code == 404

    def test_get_waveform_invalid_type(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        response = api_client.get(
            f"/api/v1/sessions/{session.id}/waveforms/invalid_type"
        )
        assert response.status_code == 422
