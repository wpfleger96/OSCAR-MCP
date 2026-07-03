"""Unit tests for ReportService."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from sqlalchemy.orm import Session as SASession

from snore.database.models import Day, Device, Session
from snore.services.report_service import ReportService
from snore.services.stats_service import StatsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_day(
    db_session: SASession,
    device: Device,
    day_date: date,
    duration_hours: float = 7.5,
    ahi: float = 3.0,
    **day_kwargs: Any,
) -> Day:
    """Create a Day with an associated Session so StatsService can aggregate it."""
    day = Day(
        device_id=device.id,
        date=day_date,
        total_therapy_hours=duration_hours,
        ahi=ahi,
        **day_kwargs,
    )
    db_session.add(day)
    db_session.flush()

    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"test_{day_date.isoformat()}_{device.id}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time())
        + timedelta(hours=duration_hours),
        duration_seconds=duration_hours * 3600,
    )
    db_session.add(sess)
    db_session.flush()
    return day


def _seed_days(
    db_session: SASession,
    device: Device,
    from_date: date,
    to_date: date,
    step_days: int = 7,
) -> None:
    """Seed one Day per step across [from_date, to_date]."""
    current = from_date
    while current <= to_date:
        _create_day(db_session, device, current)
        current += timedelta(days=step_days)
    db_session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReportService:
    """Tests for ReportService.generate_summary_report and generate_comparison_report."""

    def test_summary_starts_with_doctype_and_contains_dates(
        self, db_session, test_device
    ):
        """Generated summary HTML starts with <!DOCTYPE html> and includes the range dates."""
        today = date.today()
        from_date = today - timedelta(days=30)
        to_date = today

        service = ReportService(db_session)
        html = service.generate_summary_report(from_date, to_date)

        assert html.startswith("<!DOCTYPE html>")
        assert str(from_date) in html
        assert str(to_date) in html

    def test_summary_empty_db_renders_no_data_message(self, db_session):
        """Empty database renders a valid report with the no-data notice instead of raising."""
        today = date.today()
        from_date = today - timedelta(days=30)
        to_date = today

        service = ReportService(db_session)
        html = service.generate_summary_report(from_date, to_date)

        assert html.startswith("<!DOCTYPE html>")
        assert "No therapy data" in html

    def test_summary_with_data_contains_device_model_svg_and_monthly_row(
        self, db_session, test_device
    ):
        """With seeded data the report contains the device model, an SVG chart, and a monthly table row."""
        today = date.today()
        from_date = today - timedelta(days=60)
        to_date = today

        _seed_days(db_session, test_device, from_date, to_date, step_days=7)

        service = ReportService(db_session)
        html = service.generate_summary_report(from_date, to_date)

        assert test_device.model in html
        assert "<svg" in html
        # Monthly breakdown row — each row has a month label like "Jan 2025"
        assert "<td>" in html
        assert "days" in html.lower() or "/</td>" in html or "<td>7" in html

    def test_comparison_contains_both_range_headings_and_delta_sign(
        self, db_session, test_device
    ):
        """Comparison report includes both range headings and at least one delta cell with +/-."""
        today = date.today()
        range_a = (today - timedelta(days=60), today - timedelta(days=31))
        range_b = (today - timedelta(days=30), today)

        _seed_days(db_session, test_device, range_a[0], range_a[1], step_days=7)
        _seed_days(db_session, test_device, range_b[0], range_b[1], step_days=7)

        service = ReportService(db_session)
        html = service.generate_comparison_report(range_a, range_b)

        assert str(range_a[0]) in html
        assert str(range_b[0]) in html
        # At least one delta cell must contain a sign character
        assert "+" in html or "−" in html

    def test_comparison_one_empty_range_renders_without_raising(
        self, db_session, test_device
    ):
        """Comparison with one empty range renders successfully and shows the no-data notice."""
        today = date.today()
        range_a = (today - timedelta(days=30), today)
        range_b = (today + timedelta(days=1), today + timedelta(days=30))

        _seed_days(db_session, test_device, range_a[0], range_a[1], step_days=7)

        service = ReportService(db_session)
        html = service.generate_comparison_report(range_a, range_b)

        assert html.startswith("<!DOCTYPE html>")
        assert "No therapy data" in html

    def test_summary_autoescape_escapes_device_model_script_tag(self, db_session):
        """Device model containing <script> arrives HTML-escaped in the report."""
        import uuid

        device = Device(
            manufacturer="ACME",
            model="<script>alert(1)</script>",
            serial_number=f"XSS_{uuid.uuid4().hex[:8]}",
        )
        db_session.add(device)
        db_session.commit()

        today = date.today()
        from_date = today - timedelta(days=7)
        to_date = today

        service = ReportService(db_session)
        html = service.generate_summary_report(from_date, to_date)

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_stats_service_from_date_to_date_filters_correctly(
        self, db_session, test_device
    ):
        """get_summary(from_date=..., to_date=...) includes only in-range days."""
        today = date.today()
        in_range_date = today - timedelta(days=5)
        out_of_range_date = today - timedelta(days=30)

        _create_day(db_session, test_device, in_range_date, duration_hours=8.0, ahi=2.0)
        _create_day(
            db_session, test_device, out_of_range_date, duration_hours=6.0, ahi=10.0
        )
        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary(
            from_date=today - timedelta(days=7), to_date=today
        )

        assert summary is not None
        assert summary.days_with_data == 1
        # Only the in-range day (ahi=2.0) should be included; out-of-range (ahi=10.0) excluded
        assert summary.avg_ahi == pytest.approx(2.0, rel=1e-3)
