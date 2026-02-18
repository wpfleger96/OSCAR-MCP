class TestDevicesRouter:
    def test_list_devices_empty(self, api_client):
        response = api_client.get("/api/v1/devices/")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_devices_with_data(self, api_client, test_device):
        response = api_client.get("/api/v1/devices/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["serial_number"] == test_device.serial_number
