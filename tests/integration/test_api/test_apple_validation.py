"""Integration tests for POST /api/v1/validate/apple."""


class TestAppleValidationEndpoint:
    def test_empty_range_returns_200_with_zero_nights(self, api_client):
        response = api_client.post(
            "/api/v1/validate/apple",
            json={"from_date": "2025-01-01", "to_date": "2025-01-31"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["aggregate"]["total_nights"] == 0
        assert data["nights"] == []

    def test_malformed_body_returns_422(self, api_client):
        response = api_client.post(
            "/api/v1/validate/apple",
            json={"from_date": "not-a-date"},
        )
        assert response.status_code == 422

    def test_inverted_date_range_returns_422(self, api_client):
        """to_date < from_date must be rejected with 422."""
        response = api_client.post(
            "/api/v1/validate/apple",
            json={"from_date": "2025-01-31", "to_date": "2025-01-01"},
        )
        assert response.status_code == 422

    def test_span_over_one_year_returns_422(self, api_client):
        """A calendar span beyond the max-nights cap is rejected before running."""
        response = api_client.post(
            "/api/v1/validate/apple",
            json={"from_date": "2020-01-01", "to_date": "2021-12-31"},
        )
        assert response.status_code == 422

    def test_unowned_device_id_returns_404(self, api_client):
        """A pinned device_id the profile does not own maps to 404, not 500.

        This also pins that device_id is passed through to device resolution.
        """
        response = api_client.post(
            "/api/v1/validate/apple",
            json={
                "from_date": "2025-01-01",
                "to_date": "2025-01-05",
                "device_id": 999999,
            },
        )
        assert response.status_code == 404
        assert "device_id" in response.json()["detail"]
