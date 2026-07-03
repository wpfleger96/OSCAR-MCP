from datetime import date, timedelta

from snore.database.models import Day


class TestSummaryReport:
    def test_missing_to_date_returns_422(self, api_client):
        response = api_client.get("/api/v1/reports/summary?from_date=2025-01-01")
        assert response.status_code == 422

    def test_missing_from_date_returns_422(self, api_client):
        response = api_client.get("/api/v1/reports/summary?to_date=2025-01-31")
        assert response.status_code == 422

    def test_invalid_date_string_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/summary?from_date=not-a-date&to_date=2025-01-31"
        )
        assert response.status_code == 422

    def test_from_after_to_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/summary?from_date=2025-01-31&to_date=2025-01-01"
        )
        assert response.status_code == 422

    def test_happy_path_returns_html(self, api_client, db_session, test_device):
        start = date(2025, 1, 1)
        for i in range(3):
            db_session.add(
                Day(
                    device_id=test_device.id,
                    date=start + timedelta(days=i),
                    session_count=1,
                    total_therapy_hours=7.0,
                    ahi=2.5,
                )
            )
        db_session.flush()

        response = api_client.get(
            "/api/v1/reports/summary?from_date=2025-01-01&to_date=2025-01-31"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.lower().startswith("<!doctype html>")
        assert "attachment" in response.headers["content-disposition"]
        assert (
            "snore-report-summary-2025-01-01-2025-01-31.html"
            in response.headers["content-disposition"]
        )

    def test_no_data_range_returns_200(self, api_client):
        response = api_client.get(
            "/api/v1/reports/summary?from_date=1990-01-01&to_date=1990-01-31"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.lower().startswith("<!doctype html>")


class TestComparisonReport:
    def test_missing_to_b_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-01&to_a=2025-01-31&from_b=2025-02-01"
        )
        assert response.status_code == 422

    def test_invalid_date_string_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=bad&to_a=2025-01-31&from_b=2025-02-01&to_b=2025-02-28"
        )
        assert response.status_code == 422

    def test_range_a_from_after_to_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-31&to_a=2025-01-01&from_b=2025-02-01&to_b=2025-02-28"
        )
        assert response.status_code == 422

    def test_range_b_from_after_to_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-01&to_a=2025-01-31&from_b=2025-02-28&to_b=2025-02-01"
        )
        assert response.status_code == 422

    def test_happy_path_returns_html(self, api_client, db_session, test_device):
        for month, day_start in [(1, 1), (2, 1)]:
            db_session.add(
                Day(
                    device_id=test_device.id,
                    date=date(2025, month, day_start),
                    session_count=1,
                    total_therapy_hours=7.0,
                    ahi=2.5,
                )
            )
        db_session.flush()

        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-01&to_a=2025-01-31&from_b=2025-02-01&to_b=2025-02-28"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.lower().startswith("<!doctype html>")
        assert "attachment" in response.headers["content-disposition"]
        assert (
            "snore-report-comparison-2025-01-01-2025-01-31-vs-2025-02-01-2025-02-28.html"
            in response.headers["content-disposition"]
        )
