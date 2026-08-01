"""Dialect compilation coverage suite (§5 genericity).

Verifies that the audited SQL statements compile without error against a
PostgreSQL dialect.  These tests do NOT connect to a running PostgreSQL
instance — they use SQLAlchemy's offline compilation machinery, which
exercises the same codegen path the ORM uses at runtime.

**Runtime disclaimer:** These tests prove the statements are syntactically
valid PostgreSQL SQL as SQLAlchemy sees them.  They do NOT prove the
statements run correctly against a live PostgreSQL database — that requires
integration tests against a real server (out of scope for PR-1, which is
SQLite-only).

All statements audited here are also covered by the SQLite integration suite
so correctness on the primary dialect is verified end-to-end.
"""

from __future__ import annotations

import pytest

from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql as _pg
from sqlalchemy.engine.interfaces import Dialect


def _pg_dialect() -> Dialect:
    return _pg.dialect()  # type: ignore[no-untyped-call]


def _compile(stmt: object) -> str:
    """Compile *stmt* to a PostgreSQL SQL string."""
    compiled = getattr(stmt, "compile")(  # noqa: B009
        dialect=_pg_dialect(), compile_kwargs={"literal_binds": False}
    )
    return str(compiled)


@pytest.mark.unit
class TestDialectCompilationCoverage:
    """Compile audited ORM statements against a PostgreSQL dialect.

    Each test compiles the statement and asserts the compiled SQL contains
    the expected operator or clause — proving the typed expression maps to
    valid SQL on this dialect.

    **Runtime disclaimer**: offline compilation only; no live PostgreSQL
    connection required or used.
    """

    def test_analysis_result_delete_by_session_ids_compiles(self):
        """delete(AnalysisResult) WHERE session_id IN (...) compiles on PostgreSQL."""
        from snore.database import models

        stmt = delete(models.AnalysisResult).where(
            models.AnalysisResult.session_id.in_([1, 2, 3])
        )
        sql = _compile(stmt)
        assert "DELETE FROM" in sql.upper()
        assert "analysis_results" in sql.lower()

    def test_analysis_result_latest_ranked_subquery_compiles(self):
        """row_number() OVER (PARTITION BY session_id ORDER BY created_at DESC) compiles."""
        from snore.database import models

        ranked = (
            select(
                models.AnalysisResult.id,
                func.row_number()
                .over(
                    partition_by=models.AnalysisResult.session_id,
                    order_by=models.AnalysisResult.created_at.desc(),
                )
                .label("rn"),
            )
            .where(models.AnalysisResult.session_id.in_([1, 2]))
            .subquery()
        )
        stmt = select(ranked.c.id).where(ranked.c.rn == 1)
        sql = _compile(stmt)
        assert "ROW_NUMBER" in sql.upper()
        assert "PARTITION BY" in sql.upper()
        assert "ORDER BY" in sql.upper()

    def test_event_count_order_by_count_desc_compiles(self):
        """ORDER BY count(events.id) DESC compiles on PostgreSQL (replaces text('count DESC'))."""
        from snore.database import models

        stmt = (
            select(
                models.Event.event_type,
                func.count(models.Event.id).label("count"),
            )
            .group_by(models.Event.event_type)
            .order_by(func.count(models.Event.id).desc())
        )
        sql = _compile(stmt)
        assert "ORDER BY" in sql.upper()
        assert "DESC" in sql.upper()

    def test_orphan_cleanup_delete_not_in_sessions_compiles(self):
        """DELETE FROM waveforms WHERE session_id NOT IN (SELECT id FROM sessions) compiles."""
        from snore.database import models

        stmt = delete(models.Waveform).where(
            models.Waveform.session_id.notin_(select(models.Session.id))
        )
        sql = _compile(stmt)
        assert "DELETE FROM" in sql.upper()
        assert "NOT IN" in sql.upper()

    def test_session_count_select_compiles(self):
        """SELECT count(*) FROM sessions compiles on PostgreSQL."""
        from snore.database import models

        stmt = select(func.count()).select_from(models.Session)
        sql = _compile(stmt)
        assert "COUNT" in sql.upper()
        assert "sessions" in sql.lower()

    def test_analysis_result_ordering_by_created_at_desc_compiles(self):
        """SELECT ... ORDER BY analysis_results.created_at DESC compiles on PostgreSQL."""
        from snore.database import models

        stmt = (
            select(models.AnalysisResult)
            .where(models.AnalysisResult.session_id == 1)
            .order_by(models.AnalysisResult.created_at.desc())
            .limit(1)
        )
        sql = _compile(stmt)
        assert "ORDER BY" in sql.upper()
        assert "DESC" in sql.upper()
        assert "created_at" in sql.lower()
