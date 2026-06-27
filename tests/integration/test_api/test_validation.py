class TestValidationEndpoint:
    def test_validation_returns_200(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={"from_date": "2025-01-01", "to_date": "2025-01-31", "mode": "aasm"},
        )
        assert response.status_code == 200

    def test_validation_missing_from_date_returns_422(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={"to_date": "2025-01-31", "mode": "aasm"},
        )
        assert response.status_code == 422

    def test_validation_missing_to_date_returns_422(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={"from_date": "2025-01-01", "mode": "aasm"},
        )
        assert response.status_code == 422

    def test_validation_invalid_mode_returns_422(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={"from_date": "2025-01-01", "to_date": "2025-01-31", "mode": "bogus"},
        )
        assert response.status_code == 422

    def test_validation_aasm_relaxed_mode_accepted(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={
                "from_date": "2025-01-01",
                "to_date": "2025-01-31",
                "mode": "aasm_relaxed",
            },
        )
        assert response.status_code == 200

    def test_validation_resmed_mode_accepted(self, api_client):
        response = api_client.post(
            "/api/v1/validate",
            json={
                "from_date": "2025-01-01",
                "to_date": "2025-01-31",
                "mode": "resmed",
            },
        )
        assert response.status_code == 200
