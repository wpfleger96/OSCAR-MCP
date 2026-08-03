from datetime import date, timedelta

from snore.database.models import Day


class TestSummaryReport:
    def test_from_after_to_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/summary?from_date=2025-01-31&to_date=2025-01-01"
        )
        assert response.status_code == 422

    def test_happy_path_returns_html(self, api_client, db_session, test_device):
        start = date(2025, 1, 1)
        for i in range(3):
            db_session.add(
                Day(
                    device_id=test_device.id,
                    date=start + timedelta(days=i),
                    session_count=1,
                    total_therapy_hours=7.0,
                    ahi=2.5,
                )
            )
        db_session.flush()

        response = api_client.get(
            "/api/v1/reports/summary?from_date=2025-01-01&to_date=2025-01-31"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.lower().startswith("<!doctype html>")
        assert "attachment" in response.headers["content-disposition"]
        assert (
            "snore-report-summary-2025-01-01-2025-01-31.html"
            in response.headers["content-disposition"]
        )

    def test_no_data_range_returns_200(self, api_client):
        response = api_client.get(
            "/api/v1/reports/summary?from_date=1990-01-01&to_date=1990-01-31"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.lower().startswith("<!doctype html>")


class TestComparisonReport:
    def test_range_a_from_after_to_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-31&to_a=2025-01-01&from_b=2025-02-01&to_b=2025-02-28"
        )
        assert response.status_code == 422

    def test_range_b_from_after_to_returns_422(self, api_client):
        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-01&to_a=2025-01-31&from_b=2025-02-28&to_b=2025-02-01"
        )
        assert response.status_code == 422

    def test_happy_path_returns_html(self, api_client, db_session, test_device):
        for month, day_start in [(1, 1), (2, 1)]:
            db_session.add(
                Day(
                    device_id=test_device.id,
                    date=date(2025, month, day_start),
                    session_count=1,
                    total_therapy_hours=7.0,
                    ahi=2.5,
                )
            )
        db_session.flush()

        response = api_client.get(
            "/api/v1/reports/comparison"
            "?from_a=2025-01-01&to_a=2025-01-31&from_b=2025-02-01&to_b=2025-02-28"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.lower().startswith("<!doctype html>")
        assert "attachment" in response.headers["content-disposition"]
        assert (
            "snore-report-comparison-2025-01-01-2025-01-31-vs-2025-02-01-2025-02-28.html"
            in response.headers["content-disposition"]
        )


# ---------------------------------------------------------------------------
# Fresh-DB tests: verify local user/profile provisioning commits after report
# ---------------------------------------------------------------------------

import os

import pytest

from httpx import ASGITransport, AsyncClient

from snore.database.session import cleanup_database, session_scope


@pytest.fixture
async def fresh_report_app(tmp_path):
    """Start the app against an empty DB with the real lifespan.

    Mirrors the ``real_app`` fixture in test_transaction_semantics but scoped
    to the report tests.  The empty DB exercises the auto-provision path in
    ``ActorContextFactory.make_local()``.
    """
    db_path = tmp_path / "test_reports_fresh.db"
    os.environ["SNORE_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"

    from snore.api.app import create_app

    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        async with app.router.lifespan_context(app):
            yield client

    os.environ.pop("SNORE_DATABASE_URL", None)
    await cleanup_database()


@pytest.mark.integration
class TestReportSessionProvisioning:
    """Report route on a fresh DB must commit local user/profile provisioning.

    Regression: ReportService previously closed its injected session after
    the fetch phase.  On a fresh DB, ``ActorContextFactory.make_local()``
    auto-provisions the admin user + default profile inside the *same*
    request transaction; the service close rolled that back silently, leaving
    zero users after a 200 response.
    """

    async def test_summary_report_commits_local_user_and_profile_on_fresh_db(
        self, fresh_report_app
    ):
        """After the first summary-report GET, user and profile rows must be committed.

        The fresh DB has zero users; the request triggers ``make_local()``
        auto-provision; the report generates successfully; and the user +
        profile must survive in the DB after the response.
        """
        from sqlalchemy import func, select

        from snore.database import models

        client = fresh_report_app

        # First report request on an empty DB — triggers auto-provision.
        response = await client.get(
            "/api/v1/reports/summary?from_date=1990-01-01&to_date=1990-12-31"
        )
        assert response.status_code == 200, (
            f"Report on fresh DB returned {response.status_code}: {response.text}"
        )

        # Verify the provisioned user and profile were committed.
        async with session_scope() as verify:
            user_count = (
                await verify.execute(select(func.count()).select_from(models.User))
            ).scalar_one()
            profile_count = (
                await verify.execute(select(func.count()).select_from(models.Profile))
            ).scalar_one()

        assert user_count >= 1, (
            "Expected at least 1 User row after fresh-DB report — "
            "make_local() provisioning was rolled back"
        )
        assert profile_count >= 1, (
            "Expected at least 1 Profile row after fresh-DB report — "
            "make_local() provisioning was rolled back"
        )
