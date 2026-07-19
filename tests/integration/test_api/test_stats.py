from datetime import date, datetime, timedelta

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
        assert "ahi_trend_direction" in data


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

    def test_periods_day_type_with_data(self, api_client, db_session, test_device):
        """day granularity on /periods returns one entry per therapy date."""
        for i in range(3):
            db_session.add(
                Day(
                    device_id=test_device.id,
                    date=date(2024, 5, 1) + timedelta(days=i),
                    session_count=1,
                    total_therapy_hours=7.0,
                )
            )
        db_session.flush()

        response = api_client.get("/api/v1/stats/periods?period_type=day")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        # Each period covers exactly one day (period_start == period_end)
        for period in data:
            assert period["period_start"] == period["period_end"]


class TestStatsTrends:
    def test_trends_empty(self, api_client):
        response = api_client.get("/api/v1/stats/trends")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_trends_payload_has_13_keys(self, api_client, db_session, test_device):
        """/trends always returns exactly 13 metric keys."""
        db_session.add(
            Day(
                device_id=test_device.id,
                date=date(2024, 3, 15),
                session_count=1,
                total_therapy_hours=7.0,
                ahi=2.5,
            )
        )
        db_session.flush()

        response = api_client.get("/api/v1/stats/trends?period_type=month")
        assert response.status_code == 200
        data = response.json()

        expected_keys = {
            "ahi",
            "usage",
            "spo2",
            "leak",
            "pressure",
            "oai",
            "cai",
            "hi",
            "rera",
            "epap",
            "rr",
            "pulse",
            "mv",
        }
        assert set(data.keys()) == expected_keys

    def test_trends_day_default_limit_excludes_old_days(
        self, api_client, db_session, test_device
    ):
        """When period_type=day and no days_limit, days older than 180 are excluded."""
        old_date = date.today() - timedelta(days=200)
        recent_date = date.today() - timedelta(days=5)

        db_session.add(
            Day(
                device_id=test_device.id,
                date=old_date,
                session_count=1,
                total_therapy_hours=7.0,
                ahi=3.0,
            )
        )
        db_session.add(
            Day(
                device_id=test_device.id,
                date=recent_date,
                session_count=1,
                total_therapy_hours=7.0,
                ahi=2.0,
            )
        )
        db_session.flush()

        # No explicit days_limit → default 180 for day granularity
        response = api_client.get("/api/v1/stats/trends?period_type=day")
        assert response.status_code == 200
        data = response.json()
        ahi_dates = [entry[0] for entry in data["ahi"]]
        assert str(old_date) not in ahi_dates
        assert str(recent_date) in ahi_dates

    def test_trends_day_explicit_limit_includes_old_days(
        self, api_client, db_session, test_device
    ):
        """An explicit large days_limit overrides the day-granularity default."""
        old_date = date.today() - timedelta(days=200)

        db_session.add(
            Day(
                device_id=test_device.id,
                date=old_date,
                session_count=1,
                total_therapy_hours=7.0,
                ahi=3.0,
            )
        )
        db_session.flush()

        response = api_client.get("/api/v1/stats/trends?period_type=day&days_limit=365")
        assert response.status_code == 200
        data = response.json()
        ahi_dates = [entry[0] for entry in data["ahi"]]
        assert str(old_date) in ahi_dates


class TestStatsRecords:
    def test_records_empty(self, api_client):
        response = api_client.get("/api/v1/stats/records")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
