"""Integration tests for POST /api/v1/validate/rera."""


class TestReraValidationEndpoint:
    def test_empty_range_returns_200_with_zero_sessions(self, api_client):
        response = api_client.post(
            "/api/v1/validate/rera",
            json={"from_date": "2025-01-01", "to_date": "2025-01-31"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["aggregate"]["total_sessions"] == 0
        assert data["sessions"] == []

    def test_malformed_body_returns_422(self, api_client):
        response = api_client.post(
            "/api/v1/validate/rera",
            json={"from_date": "not-a-date"},
        )
        assert response.status_code == 422

    def test_inverted_date_range_returns_422(self, api_client):
        """to_date < from_date must be rejected with 422."""
        response = api_client.post(
            "/api/v1/validate/rera",
            json={"from_date": "2025-01-31", "to_date": "2025-01-01"},
        )
        assert response.status_code == 422

    def test_same_day_date_range_is_accepted(self, api_client):
        """Single-day range (from_date == to_date) is valid."""
        response = api_client.post(
            "/api/v1/validate/rera",
            json={"from_date": "2025-06-15", "to_date": "2025-06-15"},
        )
        assert response.status_code == 200
