from datetime import datetime


class TestAnalysisSessionsRouter:
    def test_list_analysis_sessions_empty(self, api_client):
        response = api_client.get("/api/v1/analysis/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_analysis_sessions_with_data(
        self, api_client, db_session, test_device, test_session_factory
    ):
        from snore.database.models import Day

        day = Day(
            device_id=test_device.id, date=datetime(2025, 1, 10).date(), session_count=1
        )
        db_session.add(day)
        db_session.flush()

        session = test_session_factory(
            test_device.id, start_time=datetime(2025, 1, 10, 22, 0)
        )
        session.day_id = day.id
        db_session.flush()

        response = api_client.get("/api/v1/analysis/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["session_id"] == session.id
        assert data["items"][0]["has_analysis"] is False

    def test_get_analysis_not_found(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2025, 1, 10, 22, 0)
        )
        response = api_client.get(f"/api/v1/sessions/{session.id}/analysis")
        assert response.status_code == 404

    def test_delete_analysis_empty(self, api_client):
        # Use request() since TestClient.delete() doesn't support body
        response = api_client.request(
            "DELETE",
            "/api/v1/analysis",
            json={"session_ids": []},
        )
        assert response.status_code == 200
        assert response.json()["deleted_count"] == 0


class TestAnalysisDeletePreview:
    def test_delete_preview_no_filters(self, api_client):
        response = api_client.get("/api/v1/analysis/delete-preview")
        assert response.status_code == 200
        data = response.json()
        assert data["sessions_with_analysis"] == 0
        assert data["records_to_delete"] == 0

    def test_delete_preview_with_ids(
        self, api_client, db_session, test_device, test_session_factory
    ):
        session = test_session_factory(
            test_device.id, start_time=datetime(2025, 1, 10, 22, 0)
        )
        response = api_client.get(
            f"/api/v1/analysis/delete-preview?session_ids={session.id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["sessions_with_analysis"] == 0
        assert data["records_to_delete"] == 0


class TestBatchAnalysis:
    def test_both_dates_none_returns_400(self, api_client):
        response = api_client.post("/api/v1/analysis/batch", json={})
        assert response.status_code == 400
        assert (
            "from_date" in response.json()["detail"]
            or "to_date" in response.json()["detail"]
        )

    def test_from_date_only_accepted(self, api_client):
        response = api_client.post(
            "/api/v1/analysis/batch", json={"from_date": "2025-01-01"}
        )
        assert response.status_code == 201

    def test_empty_range_returns_total_zero(self, api_client):
        response = api_client.post(
            "/api/v1/analysis/batch",
            json={"from_date": "2099-01-01", "to_date": "2099-01-31"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["total"] == 0
        assert data["successful"] == 0
        assert data["failed"] == 0
