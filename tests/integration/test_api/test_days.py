from datetime import date


class TestDaysRouter:
    def test_list_days_empty(self, api_client):
        response = api_client.get("/api/v1/days/")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_days_with_data(self, api_client, db_session, test_device):
        from snore.database.models import Day

        day = Day(
            device_id=test_device.id,
            date=date(2025, 1, 10),
            session_count=1,
            total_therapy_hours=8.0,
            ahi=2.5,
        )
        db_session.add(day)
        db_session.flush()

        response = api_client.get("/api/v1/days/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["date"] == "2025-01-10"
        assert data["items"][0]["session_count"] == 1

    def test_list_days_filter_by_device(self, api_client, db_session, test_device):
        from snore.database.models import Day

        day = Day(device_id=test_device.id, date=date(2025, 1, 10), session_count=1)
        db_session.add(day)
        db_session.flush()

        response = api_client.get(f"/api/v1/days/?device_id={test_device.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        response = api_client.get("/api/v1/days/?device_id=99999")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_get_day_not_found(self, api_client):
        response = api_client.get("/api/v1/days/2025-01-01")
        assert response.status_code == 404

    def test_get_day_with_data(self, api_client, db_session, test_device):
        from snore.database.models import Day

        day = Day(
            device_id=test_device.id,
            date=date(2025, 1, 10),
            session_count=2,
            total_therapy_hours=7.5,
            ahi=1.8,
        )
        db_session.add(day)
        db_session.flush()

        response = api_client.get("/api/v1/days/2025-01-10")
        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2025-01-10"
        assert data["session_count"] == 2
        assert data["ahi"] == 1.8
        assert data["session_ids"] == []
