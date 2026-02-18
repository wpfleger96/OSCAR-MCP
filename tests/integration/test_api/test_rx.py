class TestRxRouter:
    def test_get_rx_history_empty(self, api_client):
        response = api_client.get("/api/v1/rx/history")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_rx_current_empty(self, api_client):
        response = api_client.get("/api/v1/rx/current")
        assert response.status_code == 204

    def test_compare_rx_empty(self, api_client):
        response = api_client.get("/api/v1/rx/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["periods"] == []
        assert data["best_index"] is None
        assert data["worst_index"] is None
