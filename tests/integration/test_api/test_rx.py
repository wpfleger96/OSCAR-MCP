import uuid

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session as DbSession

from snore.database.models import Day, Device, Session, Setting


def _create_device(
    db_session: DbSession, manufacturer: str = "ResMed", model: str = "AirSense 10"
) -> Device:
    device = Device(
        manufacturer=manufacturer,
        model=model,
        serial_number=f"SN_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(device)
    db_session.flush()
    return device


def _create_day_with_session(
    db_session: DbSession,
    device: Device,
    day_date: date,
    settings: dict[str, str] | None = None,
    enabled: bool = True,
) -> Day:
    day = Day(
        device_id=device.id, date=day_date, session_count=1, total_therapy_hours=8.0
    )
    db_session.add(day)
    db_session.flush()

    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"int_{device.id}_{day_date.isoformat()}_{uuid.uuid4().hex[:4]}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time()) + timedelta(hours=8),
        duration_seconds=8 * 3600,
        enabled=enabled,
    )
    db_session.add(sess)
    db_session.flush()

    if settings:
        for key, value in settings.items():
            db_session.add(Setting(session_id=sess.id, key=key, value=value))
        db_session.flush()

    return day


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

    def test_get_rx_changes_empty(self, api_client):
        """Empty database returns 200 with empty changes list."""
        response = api_client.get("/api/v1/rx/changes")
        assert response.status_code == 200
        assert response.json() == {"changes": []}

    def test_get_rx_changes_ps_change(self, api_client, db_session):
        """Two days with different 'ps' values produce a change entry."""
        device = _create_device(db_session)
        base = date(2025, 6, 1)
        _create_day_with_session(
            db_session,
            device,
            base,
            settings={"mode": "VAuto", "ps": "4.0", "epap": "6.0"},
        )
        _create_day_with_session(
            db_session,
            device,
            base + timedelta(days=1),
            settings={"mode": "VAuto", "ps": "6.0", "epap": "6.0"},
        )
        db_session.flush()

        response = api_client.get("/api/v1/rx/changes")
        assert response.status_code == 200
        data = response.json()
        changes = data["changes"]

        ps_change = next((c for c in changes if c["key"] == "ps"), None)
        assert ps_change is not None
        assert ps_change["old_value"] == "4.0"
        assert ps_change["new_value"] == "6.0"
        assert ps_change["date"] == str(base + timedelta(days=1))
        assert ps_change["device_id"] == device.id
        assert "AirSense 10" in ps_change["device_name"]

    def test_get_rx_history_carries_device_fields(self, api_client, db_session):
        """History response includes device_id and device_name on each period."""
        device = _create_device(db_session, manufacturer="ResMed", model="AirSense 10")
        base = date(2025, 7, 1)
        for i in range(3):
            _create_day_with_session(
                db_session,
                device,
                base + timedelta(days=i),
                settings={"mode": "CPAP", "pressure_fixed": "8.0"},
            )
        db_session.flush()

        response = api_client.get("/api/v1/rx/history")
        assert response.status_code == 200
        periods = response.json()
        assert len(periods) == 1
        assert periods[0]["device_id"] == device.id
        assert periods[0]["device_name"] == "ResMed AirSense 10"


class TestRxAllRouter:
    def test_get_rx_all_empty(self, api_client):
        """Empty database returns 200 with empty history, null current, and no changes."""
        response = api_client.get("/api/v1/rx/all")
        assert response.status_code == 200
        data = response.json()
        assert data["history"] == []
        assert data["current"] is None
        assert data["best_index"] is None
        assert data["worst_index"] is None
        assert data["changes"] == {"changes": []}

    def test_get_rx_all_with_data(self, api_client, db_session):
        """Combined response contains history, current, best/worst, and changes."""
        device = _create_device(db_session)
        base = date(2025, 6, 1)

        for i in range(10):
            _create_day_with_session(
                db_session,
                device,
                base + timedelta(days=i),
                settings={
                    "mode": "APAP",
                    "pressure_min": "6.0",
                    "pressure_max": "15.0",
                },
            )

        _create_day_with_session(
            db_session,
            device,
            base + timedelta(days=10),
            settings={"mode": "CPAP", "pressure_fixed": "10.0"},
        )
        db_session.flush()

        response = api_client.get("/api/v1/rx/all")
        assert response.status_code == 200
        data = response.json()

        assert len(data["history"]) == 2
        assert data["current"] == data["history"][-1]
        assert data["current"]["settings"]["mode"] == "CPAP"

        assert "best_index" in data
        assert "worst_index" in data

        changes = data["changes"]["changes"]
        changed_keys = {c["key"] for c in changes}
        assert "mode" in changed_keys

    def test_get_rx_all_min_days_propagates(self, api_client, db_session):
        """min_days parameter affects best/worst index selection."""
        device = _create_device(db_session)
        base = date(2025, 6, 1)

        for i in range(3):
            _create_day_with_session(
                db_session,
                device,
                base + timedelta(days=i),
                settings={"mode": "APAP", "pressure_min": "6.0"},
            )
        db_session.flush()

        response_high = api_client.get("/api/v1/rx/all?min_days=100")
        data_high = response_high.json()
        assert data_high["best_index"] is None
        assert data_high["worst_index"] is None

        response_low = api_client.get("/api/v1/rx/all?min_days=1")
        data_low = response_low.json()
        assert len(data_low["history"]) == 1
