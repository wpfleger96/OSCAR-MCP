"""End-to-end regression: demo import persists a Statistics row.

Guards the full demo path — ``DemoService.import_from_fixtures`` →
``parse_night_session`` → ``_shift_session`` → ``import_sessions_batch`` — which
skipped the ``statistics`` row whenever the parsed session arrived with
``has_statistics`` False. Mirrors the production wiring in ``app.py`` (import in
one ``session_scope``, read back in a fresh one).
"""

from __future__ import annotations

from sqlalchemy import select

from snore.database import models
from snore.database.session import init_database, session_scope
from snore.services.demo_service import DemoService
from tests.helpers.fixtures_loader import get_fixture_path


class TestDemoImportStatistics:
    async def test_demo_import_persists_statistics_row(self, temp_db):
        """A demo fixture night lands a Statistics row with usable indices."""
        await init_database(str(temp_db))
        # A real multi-segment recorded night (mask removals) with event data.
        fixtures_dir = get_fixture_path("20250910")

        async with session_scope() as db:
            counts = await DemoService(db).import_from_fixtures(fixtures_dir)

        assert counts["sessions"] >= 1

        async with session_scope() as db:
            stats = (await db.execute(select(models.Statistics))).scalars().all()

        assert stats, "demo import must persist at least one Statistics row"
        row = stats[0]
        assert row.ahi is not None
        assert row.usage_hours is not None
        assert row.usage_hours > 0
