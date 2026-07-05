from datetime import datetime
from unittest.mock import patch


class TestDbStats:
    def test_stats_returns_200(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.get("/api/v1/db/stats")
        assert response.status_code == 200

    def test_stats_excludes_db_path(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert "db_path" not in data

    def test_stats_empty_db_counts_zero(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert data["session_count"] == 0
        assert data["device_count"] == 0

    def test_stats_counts_after_insert(
        self, api_client, db_session, test_device, test_session_factory, temp_db
    ):
        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert data["device_count"] >= 1
        assert data["session_count"] >= 1

    def test_stats_has_expected_keys(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert "size_mb" in data
        assert "session_count" in data
        assert "waveform_coverage_pct" in data


class TestDbVacuum:
    def test_vacuum_returns_200(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/vacuum")
        assert response.status_code == 200

    def test_vacuum_result_shape(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/vacuum")
        data = response.json()
        assert "status" in data
        assert "size_before_mb" in data
        assert "size_after_mb" in data

    def test_vacuum_status_is_success(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/vacuum")
        assert response.json()["status"] == "success"


class TestDbReset:
    def test_reset_returns_200(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/reset")
        assert response.status_code == 200

    def test_reset_result_shape(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/reset")
        data = response.json()
        assert "status" in data
        assert "tables_cleared" in data
        assert "total_rows_deleted" in data
        assert "size_before_mb" in data
        assert "size_after_mb" in data

    def test_reset_status_is_success(self, api_client, temp_db):
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/reset")
        assert response.json()["status"] == "success"

    def test_reset_clears_data(
        self, api_client, db_session, test_device, test_session_factory, temp_db
    ):
        from datetime import datetime

        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()
        with patch("snore.api.routers.db.get_db_path", return_value=str(temp_db)):
            response = api_client.post("/api/v1/db/reset")
        assert response.json()["total_rows_deleted"] >= 1
