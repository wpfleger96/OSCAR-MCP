class TestValidationEndpoint:
    def test_validation_returns_200(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={"from_date": "2025-01-01", "to_date": "2025-01-31", "mode": "aasm"},
        )
        assert response.status_code == 200

    def test_validation_invalid_mode_returns_422(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={"from_date": "2025-01-01", "to_date": "2025-01-31", "mode": "bogus"},
        )
        assert response.status_code == 422
