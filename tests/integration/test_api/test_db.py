import uuid

from datetime import datetime
from unittest.mock import patch

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client


def _patch_target(temp_db: object) -> object:
    """Context manager that patches the DB router to use the test database."""
    from snore.database.target import DatabaseTarget

    target = DatabaseTarget.from_url(str(temp_db))
    return patch("snore.api.routers.db._get_target", return_value=target)


def _member_actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=999,
        role=Role.MEMBER,
        mode=AuthMode.LOCAL,
    )


class TestDbStats:
    def test_stats_excludes_db_path(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert "db_path" not in data

    def test_stats_empty_db_counts_zero(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert data["session_count"] == 0
        assert data["device_count"] == 0

    def test_stats_counts_after_insert(
        self, api_client, db_session, test_device, test_session_factory, temp_db
    ):
        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert data["device_count"] >= 1
        assert data["session_count"] >= 1

    def test_admin_gets_200(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        assert response.status_code == 200

    def test_member_gets_403(self, async_db_session, db_session, temp_db):
        member = models.User(
            canonical_email=f"member_{uuid.uuid4().hex[:8]}@test.local",
            role="member",
        )
        db_session.add(member)
        db_session.flush()
        client = make_test_client(async_db_session, actor=_member_actor(member.id))
        with _patch_target(temp_db):
            response = client.get("/api/v1/db/stats")
        assert response.status_code == 403


class TestDbVacuum:
    def test_vacuum_status_is_success(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/vacuum")
        assert response.json()["status"] == "success"


class TestDbReset:
    def test_reset_status_is_success(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/reset")
        assert response.json()["status"] == "success"

    def test_reset_clears_data(
        self, api_client, db_session, test_device, test_session_factory, temp_db
    ):
        from datetime import datetime

        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/reset")
        assert response.json()["total_rows_deleted"] >= 1
