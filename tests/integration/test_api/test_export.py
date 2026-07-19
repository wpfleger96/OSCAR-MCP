import shutil
import zipfile

from pathlib import Path
from unittest.mock import patch


def _make_fake_csv_export(
    self: object, db: object, output: Path, **kwargs: object
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("date,ahi\n")


class TestExportCsv:
    def test_csv_returns_200(self, api_client):
        with patch(
            "snore.api.routers.export.ExportService.export_csv",
            _make_fake_csv_export,
        ):
            response = api_client.get("/api/v1/export/csv")
        assert response.status_code == 200

    def test_csv_content_type(self, api_client):
        with patch(
            "snore.api.routers.export.ExportService.export_csv",
            _make_fake_csv_export,
        ):
            response = api_client.get("/api/v1/export/csv")
        assert "text/csv" in response.headers["content-type"]

    def test_csv_content_disposition(self, api_client):
        with patch(
            "snore.api.routers.export.ExportService.export_csv",
            _make_fake_csv_export,
        ):
            response = api_client.get("/api/v1/export/csv")
        assert "snore_export.csv" in response.headers.get("content-disposition", "")


class TestExportJson:
    def test_json_returns_200(self, api_client):
        response = api_client.get("/api/v1/export/json")
        assert response.status_code == 200

    def test_json_content_type(self, api_client):
        response = api_client.get("/api/v1/export/json")
        assert "application/json" in response.headers["content-type"]

    def test_json_content_disposition(self, api_client):
        response = api_client.get("/api/v1/export/json")
        assert "snore_export.json" in response.headers.get("content-disposition", "")


class TestExportRaw:
    def test_raw_returns_200(self, api_client, tmp_path):
        """Mock ExportService.export_raw to write a dummy zip file."""
        dummy_zip = tmp_path / "dummy.zip"
        with zipfile.ZipFile(dummy_zip, "w") as zf:
            zf.writestr("dummy.txt", "test content")

        def fake_export_raw(self, output, **kwargs):
            shutil.copy(dummy_zip, output)
            return type("R", (), {"output_path": output})()

        with patch(
            "snore.api.routers.export.ExportService.export_raw",
            fake_export_raw,
        ):
            response = api_client.get("/api/v1/export/raw")
        assert response.status_code == 200

    def test_raw_content_type(self, api_client, tmp_path):
        dummy_zip = tmp_path / "dummy.zip"
        with zipfile.ZipFile(dummy_zip, "w") as zf:
            zf.writestr("dummy.txt", "test")

        def fake_export_raw(self, output, **kwargs):
            shutil.copy(dummy_zip, output)
            return type("R", (), {"output_path": output})()

        with patch(
            "snore.api.routers.export.ExportService.export_raw",
            fake_export_raw,
        ):
            response = api_client.get("/api/v1/export/raw")
        assert "application/zip" in response.headers["content-type"]
