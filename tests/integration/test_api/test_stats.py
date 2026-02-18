from datetime import date, datetime

import pytest

from snore.database.models import Day


class TestStatsSummary:
    def test_summary_empty(self, api_client):
        response = api_client.get("/api/v1/stats/summary")
        assert response.status_code == 204

    def test_summary_with_data(
        self, api_client, db_session, test_device, test_session_factory
    ):
        _session = test_session_factory(
            test_device.id,
            start_time=datetime(2024, 1, 1, 22, 0),
            ahi=3.5,
            usage_hours=7.0,
        )
        day = Day(
            device_id=test_device.id,
            date=date(2024, 1, 1),
            session_count=1,
            total_therapy_hours=7.0,
        )
        db_session.add(day)
        db_session.flush()

        response = api_client.get("/api/v1/stats/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["days_with_data"] == 1
        assert data["total_hours"] == pytest.approx(0.0, abs=0.1)


class TestStatsPeriods:
    def test_periods_empty(self, api_client):
        response = api_client.get("/api/v1/stats/periods")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_periods_with_data(self, api_client, db_session, test_device):
        day = Day(
            device_id=test_device.id,
            date=date(2024, 1, 15),
            session_count=1,
            total_therapy_hours=7.0,
        )
        db_session.add(day)
        db_session.flush()

        response = api_client.get("/api/v1/stats/periods?period_type=month")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        period = data[0]
        assert "period_type" in period
        assert "period_start" in period
        assert "period_end" in period

    def test_periods_week_type(self, api_client):
        response = api_client.get("/api/v1/stats/periods?period_type=week")
        assert response.status_code == 200

    def test_periods_year_type(self, api_client):
        response = api_client.get("/api/v1/stats/periods?period_type=year")
        assert response.status_code == 200


class TestStatsTrends:
    def test_trends_empty(self, api_client):
        response = api_client.get("/api/v1/stats/trends")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_trends_with_period_type(self, api_client):
        response = api_client.get("/api/v1/stats/trends?period_type=week")
        assert response.status_code == 200


class TestStatsRecords:
    def test_records_empty(self, api_client):
        response = api_client.get("/api/v1/stats/records")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_records_top_n(self, api_client):
        response = api_client.get("/api/v1/stats/records?top_n=3")
        assert response.status_code == 200
