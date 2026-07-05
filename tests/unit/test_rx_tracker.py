"""Unit tests for RxTracker high-level period queries."""

import uuid

from datetime import date, datetime, timedelta

import pytest

from sqlalchemy.orm import Session as DbSession

from snore.analysis.rx_tracker import RxTracker, _diff_settings
from snore.database.models import Day, Device, Session, Setting


def _create_device(
    db_session: DbSession, manufacturer: str = "ResMed", model: str = "AirSense 10"
) -> Device:
    """Create a device with a unique serial number."""
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
    ahi: float | None = None,
    total_therapy_hours: float = 8.0,
    settings: dict | None = None,
    enabled: bool = True,
) -> Day:
    """Helper to create a Day with a linked Session and optional RX settings."""
    day = Day(
        device_id=device.id,
        date=day_date,
        session_count=1,
        total_therapy_hours=total_therapy_hours,
        ahi=ahi,
        leak_median=5.0,
    )
    db_session.add(day)
    db_session.flush()

    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"rx_test_{device.id}_{day_date.isoformat()}_{uuid.uuid4().hex[:4]}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time())
        + timedelta(hours=total_therapy_hours),
        duration_seconds=total_therapy_hours * 3600,
        enabled=enabled,
    )
    db_session.add(sess)
    db_session.flush()

    if settings:
        for key, value in settings.items():
            db_session.add(Setting(session_id=sess.id, key=key, value=value))
        db_session.flush()

    return day


RX_SETTINGS = {
    "mode": "APAP",
    "pressure_min": "4.0",
    "pressure_max": "20.0",
}


class TestRxTrackerHistory:
    def test_history_empty_db(self, db_session):
        """Empty database returns empty list."""
        tracker = RxTracker()
        result = tracker.get_history(db_session)
        assert result == []

    def test_history_single_period(self, db_session, test_device):
        """Single cohesive RX period is returned as one entry."""
        base = date(2025, 1, 1)
        for i in range(10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=3.0 + i * 0.1,
                settings=RX_SETTINGS,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_history(db_session)

        assert len(result) == 1
        assert result[0].days_count == 10
        assert result[0].settings == RX_SETTINGS
        assert result[0].start_date == base
        assert result[0].end_date == base + timedelta(days=9)
        assert result[0].device_id == test_device.id
        assert result[0].device_name == "Test Manufacturer Test Model"

    def test_history_two_periods_different_settings(self, db_session, test_device):
        """Settings change creates two distinct periods."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "6.0", "pressure_max": "20.0"}

        for i in range(5):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=5.0,
                settings=settings_a,
            )
        for i in range(5, 10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=3.0,
                settings=settings_b,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_history(db_session)

        assert len(result) == 2
        assert result[0].days_count == 5
        assert result[1].days_count == 5

    def test_history_stats_computed(self, db_session, test_device):
        """Stats fields are populated for periods with data."""
        base = date(2025, 3, 1)
        for i in range(7):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=4.0,
                total_therapy_hours=7.5,
                settings=RX_SETTINGS,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_history(db_session)

        assert len(result) == 1
        assert result[0].avg_ahi == pytest.approx(4.0)
        assert result[0].median_ahi == pytest.approx(4.0)
        assert result[0].avg_hours == pytest.approx(7.5)
        assert result[0].total_hours == pytest.approx(7.5 * 7)

    def test_history_two_devices_same_settings_produce_separate_periods(
        self, db_session
    ):
        """Two devices with identical settings on the same dates yield two separate periods."""
        device_a = _create_device(
            db_session, manufacturer="ResMed", model="AirSense 10"
        )
        device_b = _create_device(
            db_session, manufacturer="ResMed", model="AirCurve 10"
        )
        base = date(2025, 6, 1)
        settings = {"mode": "CPAP", "pressure_fixed": "10.0"}

        for i in range(5):
            _create_day_with_session(
                db_session, device_a, base + timedelta(days=i), settings=settings
            )
            _create_day_with_session(
                db_session, device_b, base + timedelta(days=i), settings=settings
            )
        db_session.flush()

        result = RxTracker().get_history(db_session)

        assert len(result) == 2
        device_ids = {r.device_id for r in result}
        assert device_ids == {device_a.id, device_b.id}
        for r in result:
            assert r.days_count == 5
            if r.device_id == device_a.id:
                assert r.device_name == "ResMed AirSense 10"
            else:
                assert r.device_name == "ResMed AirCurve 10"

    def test_history_ps_change_splits_period(self, db_session, test_device):
        """Changing the 'ps' bilevel key splits the RX period."""
        base = date(2025, 4, 1)
        settings_before = {"mode": "VAuto", "ps": "4.0", "epap": "6.0"}
        settings_after = {"mode": "VAuto", "ps": "6.0", "epap": "6.0"}

        for i in range(3):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=settings_before,
            )
        for i in range(3, 6):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=settings_after,
            )
        db_session.flush()

        result = RxTracker().get_history(db_session)

        assert len(result) == 2
        assert result[0].settings["ps"] == "4.0"
        assert result[1].settings["ps"] == "6.0"

    def test_history_disabled_session_ignored(self, db_session, test_device):
        """Disabled sessions are not used to extract RX settings."""
        base = date(2025, 5, 1)
        settings_enabled = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_disabled = {"mode": "APAP", "pressure_min": "4.0"}

        day = Day(
            device_id=test_device.id,
            date=base,
            session_count=2,
            total_therapy_hours=8.0,
        )
        db_session.add(day)
        db_session.flush()

        # Enabled session — longer, contributes settings
        sess_enabled = Session(
            device_id=test_device.id,
            day_id=day.id,
            device_session_id=f"enabled_{uuid.uuid4().hex[:4]}",
            start_time=datetime.combine(base, datetime.min.time()),
            end_time=datetime.combine(base, datetime.min.time()) + timedelta(hours=8),
            duration_seconds=8 * 3600,
            enabled=True,
        )
        db_session.add(sess_enabled)
        db_session.flush()
        for k, v in settings_enabled.items():
            db_session.add(Setting(session_id=sess_enabled.id, key=k, value=v))

        # Disabled session — different settings, should be ignored
        sess_disabled = Session(
            device_id=test_device.id,
            day_id=day.id,
            device_session_id=f"disabled_{uuid.uuid4().hex[:4]}",
            start_time=datetime.combine(base, datetime.min.time()),
            end_time=datetime.combine(base, datetime.min.time()) + timedelta(hours=6),
            duration_seconds=6 * 3600,
            enabled=False,
        )
        db_session.add(sess_disabled)
        db_session.flush()
        for k, v in settings_disabled.items():
            db_session.add(Setting(session_id=sess_disabled.id, key=k, value=v))

        db_session.flush()

        result = RxTracker().get_history(db_session)

        assert len(result) == 1
        assert result[0].settings == settings_enabled

    def test_history_day_with_only_disabled_sessions_skipped(
        self, db_session, test_device
    ):
        """A day whose only sessions are disabled contributes no period days."""
        base = date(2025, 7, 1)
        # Day 0: all disabled — skipped
        day_disabled = Day(
            device_id=test_device.id,
            date=base,
            session_count=1,
            total_therapy_hours=4.0,
        )
        db_session.add(day_disabled)
        db_session.flush()
        sess_off = Session(
            device_id=test_device.id,
            day_id=day_disabled.id,
            device_session_id=f"off_{uuid.uuid4().hex[:4]}",
            start_time=datetime.combine(base, datetime.min.time()),
            end_time=datetime.combine(base, datetime.min.time()) + timedelta(hours=4),
            duration_seconds=4 * 3600,
            enabled=False,
        )
        db_session.add(sess_off)
        db_session.flush()
        db_session.add(Setting(session_id=sess_off.id, key="mode", value="CPAP"))
        db_session.flush()

        # Day 1: enabled
        _create_day_with_session(
            db_session, test_device, base + timedelta(days=1), settings=RX_SETTINGS
        )
        db_session.flush()

        result = RxTracker().get_history(db_session)

        assert len(result) == 1
        assert result[0].start_date == base + timedelta(days=1)


class TestRxTrackerCurrent:
    def test_current_empty_db(self, db_session):
        """Empty database returns None."""
        tracker = RxTracker()
        result = tracker.get_current(db_session)
        assert result is None

    def test_current_returns_last_period(self, db_session, test_device):
        """Returns the most recent RX period when multiple exist."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "6.0", "pressure_max": "20.0"}

        for i in range(5):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=settings_a,
            )
        for i in range(5, 10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=settings_b,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_current(db_session)

        assert result is not None
        assert result.settings == settings_b
        assert result.end_date == base + timedelta(days=9)


class TestRxTrackerCurrentTailWalk:
    def test_current_empty_db_returns_none(self, db_session: DbSession) -> None:
        """Empty database returns None."""
        assert RxTracker().get_current(db_session) is None

    def test_current_equals_history_last_single_device(
        self, db_session: DbSession, test_device: Device
    ) -> None:
        """Single device with multiple periods: get_current equals get_history()[-1]."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "6.0", "pressure_max": "20.0"}
        for i in range(5):
            _create_day_with_session(
                db_session, test_device, base + timedelta(days=i), settings=settings_a
            )
        for i in range(5, 10):
            _create_day_with_session(
                db_session, test_device, base + timedelta(days=i), settings=settings_b
            )
        db_session.flush()

        tracker = RxTracker()
        assert tracker.get_current(db_session) == tracker.get_history(db_session)[-1]

    def test_current_equals_history_last_multi_device(
        self, db_session: DbSession
    ) -> None:
        """Two devices with interleaved dates: get_current equals get_history()[-1]."""
        device_a = _create_device(
            db_session, manufacturer="ResMed", model="AirSense 10"
        )
        device_b = _create_device(
            db_session, manufacturer="ResMed", model="AirCurve 10"
        )
        base = date(2025, 3, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "10.0"}
        settings_b = {"mode": "APAP", "pressure_min": "5.0", "pressure_max": "20.0"}

        for i in range(7):
            _create_day_with_session(
                db_session, device_a, base + timedelta(days=i), settings=settings_a
            )
        # device_b starts later, so its period will have the higher start_date
        for i in range(14, 21):
            _create_day_with_session(
                db_session, device_b, base + timedelta(days=i), settings=settings_b
            )
        db_session.flush()

        tracker = RxTracker()
        assert tracker.get_current(db_session) == tracker.get_history(db_session)[-1]

    def test_current_later_start_on_lower_device_id_wins(
        self, db_session: DbSession
    ) -> None:
        """A later start_date wins over a higher device_id with an earlier start."""
        device_low = _create_device(
            db_session, manufacturer="ResMed", model="AirSense 10"
        )
        device_high = _create_device(
            db_session, manufacturer="ResMed", model="AirCurve 10"
        )
        assert device_low.id < device_high.id

        base = date(2025, 5, 1)
        settings_low = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_high = {"mode": "APAP", "pressure_min": "4.0", "pressure_max": "20.0"}

        # device_high starts at base (earlier), device_low starts 10 days later
        for i in range(5):
            _create_day_with_session(
                db_session,
                device_high,
                base + timedelta(days=i),
                settings=settings_high,
            )
        for i in range(10, 15):
            _create_day_with_session(
                db_session, device_low, base + timedelta(days=i), settings=settings_low
            )
        db_session.flush()

        result = RxTracker().get_current(db_session)
        assert result is not None
        assert result.device_id == device_low.id
        assert result.start_date == base + timedelta(days=10)

    def test_current_skips_none_settings_days_at_tail(
        self, db_session: DbSession, test_device: Device
    ) -> None:
        """Newest days with no valid settings are skipped; real period below is returned."""
        base = date(2025, 6, 1)
        real_settings = {"mode": "CPAP", "pressure_fixed": "8.0"}

        # Days 0–4: real period with valid settings
        for i in range(5):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=real_settings,
            )
        # Days 5–7: disabled sessions → _get_day_rx_settings returns None
        for i in range(5, 8):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=real_settings,
                enabled=False,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_current(db_session)
        assert result is not None
        assert result == tracker.get_history(db_session)[-1]

    def test_current_skips_none_settings_day_mid_period(
        self, db_session: DbSession, test_device: Device
    ) -> None:
        """A None-settings day sandwiched inside the last period is skipped, not a boundary."""
        base = date(2025, 7, 1)
        settings_old = {"mode": "CPAP", "pressure_fixed": "6.0"}
        settings_new = {"mode": "APAP", "pressure_min": "4.0", "pressure_max": "20.0"}

        # Older different-fingerprint days
        for i in range(3):
            _create_day_with_session(
                db_session, test_device, base + timedelta(days=i), settings=settings_old
            )
        # Newer period: valid day, then a None-settings day, then a valid day (newest)
        _create_day_with_session(
            db_session, test_device, base + timedelta(days=3), settings=settings_new
        )
        _create_day_with_session(
            db_session,
            test_device,
            base + timedelta(days=4),
            settings=settings_new,
            enabled=False,
        )
        _create_day_with_session(
            db_session, test_device, base + timedelta(days=5), settings=settings_new
        )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_current(db_session)
        assert result is not None
        assert result == tracker.get_history(db_session)[-1]

    def test_current_all_days_same_settings_single_period(
        self, db_session: DbSession, test_device: Device
    ) -> None:
        """When fingerprint never changes, all batches exhaust and whole history is one period."""
        base = date(2025, 2, 1)
        settings = {"mode": "CPAP", "pressure_fixed": "10.0"}
        for i in range(30):
            _create_day_with_session(
                db_session, test_device, base + timedelta(days=i), settings=settings
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_current(db_session)
        assert result is not None
        assert result.days_count == 30
        assert result == tracker.get_history(db_session)[-1]

    def test_current_period_spanning_batch_boundary(
        self, db_session: DbSession, test_device: Device
    ) -> None:
        """Current period >90 days spans a batch boundary; result equals get_history()[-1]."""
        base = date(2024, 1, 1)
        settings_old = {"mode": "CPAP", "pressure_fixed": "6.0"}
        settings_new = {"mode": "APAP", "pressure_min": "4.0", "pressure_max": "20.0"}

        # 10 older days with different settings
        for i in range(10):
            _create_day_with_session(
                db_session, test_device, base + timedelta(days=i), settings=settings_old
            )
        # 95 consecutive days with current settings (crosses the 90-day batch boundary)
        new_start = base + timedelta(days=10)
        for i in range(95):
            _create_day_with_session(
                db_session,
                test_device,
                new_start + timedelta(days=i),
                settings=settings_new,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_current(db_session)
        assert result is not None
        assert result.start_date == new_start
        assert result.days_count == 95
        assert result == tracker.get_history(db_session)[-1]


class TestRxTrackerComparison:
    def test_comparison_empty_db(self, db_session):
        """Empty database returns empty comparison."""
        tracker = RxTracker()
        result = tracker.get_comparison(db_session)
        assert result.periods == []
        assert result.best_index is None
        assert result.worst_index is None

    def test_comparison_insufficient_days(self, db_session, test_device):
        """Periods with fewer days than min_days are excluded from best/worst."""
        base = date(2025, 1, 1)
        for i in range(3):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=2.0,
                settings=RX_SETTINGS,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_comparison(db_session, min_days=7)

        assert len(result.periods) == 1
        # Period has fewer days than min_days threshold, so no best/worst
        assert result.best_index is None
        assert result.worst_index is None

    def test_comparison_best_worst_identified(self, db_session, test_device):
        """Best (lowest AHI) and worst (highest AHI) periods identified correctly."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "5.0", "pressure_max": "20.0"}
        settings_c = {"mode": "APAP", "pressure_min": "8.0", "pressure_max": "20.0"}

        for i in range(10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=5.0,
                settings=settings_a,
            )
        for i in range(10, 20):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=1.5,
                settings=settings_b,
            )
        for i in range(20, 30):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=8.0,
                settings=settings_c,
            )
        db_session.flush()

        tracker = RxTracker()
        result = tracker.get_comparison(db_session, min_days=7)

        assert len(result.periods) == 3
        assert result.best_index == 1
        assert result.worst_index == 2


class TestDiffSettings:
    def test_empty_dicts(self):
        assert _diff_settings({}, {}) == []

    def test_no_change(self):
        s = {"mode": "CPAP", "pressure_fixed": "8.0"}
        assert _diff_settings(s, s.copy()) == []

    def test_value_changed(self):
        prev = {"mode": "CPAP", "pressure_fixed": "8.0"}
        curr = {"mode": "CPAP", "pressure_fixed": "10.0"}
        result = _diff_settings(prev, curr)
        assert result == [("pressure_fixed", "8.0", "10.0")]

    def test_key_added(self):
        result = _diff_settings({}, {"ps": "4.0"})
        assert result == [("ps", None, "4.0")]

    def test_key_removed(self):
        result = _diff_settings({"ps": "4.0"}, {})
        assert result == [("ps", "4.0", None)]

    def test_multiple_changes_sorted_by_key(self):
        prev = {"mode": "CPAP", "ps": "4.0", "epap": "6.0"}
        curr = {"mode": "VAuto", "ps": "6.0", "epap": "6.0"}
        result = _diff_settings(prev, curr)
        keys = [r[0] for r in result]
        assert keys == sorted(keys)
        assert ("mode", "CPAP", "VAuto") in result
        assert ("ps", "4.0", "6.0") in result


class TestRxTrackerChanges:
    def test_changes_empty_db(self, db_session):
        """Empty database returns empty changes list."""
        result = RxTracker().get_changes(db_session)
        assert result.changes == []

    def test_single_day_emits_no_changes(self, db_session, test_device):
        """First day with settings has no previous to diff against."""
        _create_day_with_session(
            db_session,
            test_device,
            date(2025, 1, 1),
            settings={"mode": "CPAP", "pressure_fixed": "8.0"},
        )
        db_session.flush()

        result = RxTracker().get_changes(db_session)
        assert result.changes == []

    def test_multi_key_change_emits_one_entry_per_key(self, db_session, test_device):
        """A day where two keys change yields two separate RxSettingChange entries."""
        base = date(2025, 2, 1)
        _create_day_with_session(
            db_session,
            test_device,
            base,
            settings={"mode": "CPAP", "pressure_fixed": "8.0"},
        )
        _create_day_with_session(
            db_session,
            test_device,
            base + timedelta(days=1),
            settings={"mode": "APAP", "pressure_min": "6.0"},
        )
        db_session.flush()

        result = RxTracker().get_changes(db_session)
        keys = {c.key for c in result.changes}
        assert "mode" in keys
        assert len(result.changes) >= 2
        for c in result.changes:
            assert c.date == base + timedelta(days=1)
            assert c.device_id == test_device.id

    def test_days_without_settings_are_bridged(self, db_session, test_device):
        """A gap day (no enabled sessions with settings) is skipped; diff uses last day WITH settings."""
        base = date(2025, 3, 1)
        # Day 0: settings A
        _create_day_with_session(
            db_session,
            test_device,
            base,
            settings={"mode": "CPAP", "pressure_fixed": "8.0"},
        )
        # Day 1: no settings (only disabled session)
        day_gap = Day(
            device_id=test_device.id,
            date=base + timedelta(days=1),
            session_count=1,
            total_therapy_hours=0.0,
        )
        db_session.add(day_gap)
        db_session.flush()
        sess_gap = Session(
            device_id=test_device.id,
            day_id=day_gap.id,
            device_session_id=f"gap_{uuid.uuid4().hex[:4]}",
            start_time=datetime.combine(base + timedelta(days=1), datetime.min.time()),
            end_time=datetime.combine(base + timedelta(days=1), datetime.min.time())
            + timedelta(hours=1),
            duration_seconds=3600,
            enabled=False,
        )
        db_session.add(sess_gap)
        db_session.flush()
        # Day 2: settings B (change from day 0)
        _create_day_with_session(
            db_session,
            test_device,
            base + timedelta(days=2),
            settings={"mode": "APAP", "pressure_min": "4.0"},
        )
        db_session.flush()

        result = RxTracker().get_changes(db_session)
        # Only day 2 triggers diffs (day 1 is skipped), so changes happen at day 2
        assert all(c.date == base + timedelta(days=2) for c in result.changes)
        assert len(result.changes) >= 1

    def test_changes_across_two_devices_are_independent(self, db_session):
        """Changes from different devices are not cross-diffed."""
        device_a = _create_device(
            db_session, manufacturer="ResMed", model="AirSense 10"
        )
        device_b = _create_device(
            db_session, manufacturer="ResMed", model="AirCurve 10"
        )
        base = date(2025, 4, 1)

        # Device A: CPAP → APAP
        _create_day_with_session(
            db_session,
            device_a,
            base,
            settings={"mode": "CPAP", "pressure_fixed": "8.0"},
        )
        _create_day_with_session(
            db_session,
            device_a,
            base + timedelta(days=1),
            settings={"mode": "APAP", "pressure_min": "6.0"},
        )
        # Device B: stable settings, no change
        _create_day_with_session(
            db_session,
            device_b,
            base,
            settings={"mode": "CPAP", "pressure_fixed": "8.0"},
        )
        _create_day_with_session(
            db_session,
            device_b,
            base + timedelta(days=1),
            settings={"mode": "CPAP", "pressure_fixed": "8.0"},
        )
        db_session.flush()

        result = RxTracker().get_changes(db_session)
        # All changes should be from device_a only
        assert all(c.device_id == device_a.id for c in result.changes)
        assert len(result.changes) >= 1

    def test_changes_sorted_by_date_device_key(self, db_session):
        """Changes are sorted (date, device_id, key) ascending."""
        device_a = _create_device(
            db_session, manufacturer="ResMed", model="AirSense 10"
        )
        device_b = _create_device(
            db_session, manufacturer="ResMed", model="AirCurve 10"
        )
        base = date(2025, 5, 1)

        for device in [device_a, device_b]:
            _create_day_with_session(
                db_session, device, base, settings={"mode": "CPAP", "ps": "4.0"}
            )
            _create_day_with_session(
                db_session,
                device,
                base + timedelta(days=1),
                settings={"mode": "APAP", "ps": "6.0"},
            )
        db_session.flush()

        result = RxTracker().get_changes(db_session)
        changes = result.changes
        for i in range(len(changes) - 1):
            a, b = changes[i], changes[i + 1]
            assert (a.date, a.device_id, a.key) <= (b.date, b.device_id, b.key)

    def test_non_rx_key_change_is_tracked(self, db_session, test_device):
        """A comfort-setting change (humidity_level) not in RX_KEYS appears in get_changes."""
        base = date(2025, 6, 1)
        _create_day_with_session(
            db_session,
            test_device,
            base,
            settings={"mode": "CPAP", "pressure_fixed": "8.0", "humidity_level": "3"},
        )
        _create_day_with_session(
            db_session,
            test_device,
            base + timedelta(days=1),
            settings={"mode": "CPAP", "pressure_fixed": "8.0", "humidity_level": "5"},
        )
        db_session.flush()

        result = RxTracker().get_changes(db_session)

        humidity_changes = [c for c in result.changes if c.key == "humidity_level"]
        assert len(humidity_changes) == 1
        change = humidity_changes[0]
        assert change.date == base + timedelta(days=1)
        assert change.old_value == "3"
        assert change.new_value == "5"
        assert change.device_id == test_device.id
