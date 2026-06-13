"""Drift guards keeping Pydantic statistics schemas aligned with the ORM.

The importer splats ``SessionStatistics.model_dump()`` directly into the
``models.Statistics`` constructor, and the session service hydrates the
services-layer ``SessionStatistics`` from ORM rows via ``model_validate``.
Both shortcuts are only safe while every Pydantic field maps to an ORM
column of the same name; these tests make that invariant executable.
"""

import pytest

from snore.database import models
from snore.parsers.unified import SessionStatistics as UnifiedSessionStatistics
from snore.services.schemas import SessionStatistics as ServiceSessionStatistics

pytestmark = pytest.mark.unit

# ORM columns that are intentionally not sourced from the unified statistics
# model. ``session_id`` is the primary/foreign key, set explicitly by the
# importer (``models.Statistics(session_id=..., **stats.model_dump())``) rather
# than carried in the Pydantic payload. Anything else missing from the unified
# model would be silently never imported, so it must be added here deliberately.
_IMPORTER_SUPPLIED_COLUMNS = {"session_id"}


def _statistics_column_names() -> set[str]:
    return {column.name for column in models.Statistics.__table__.columns}


def test_unified_statistics_fields_subset_of_orm_columns() -> None:
    """Every unified-parser statistics field must exist as an ORM column."""
    missing = set(UnifiedSessionStatistics.model_fields) - _statistics_column_names()
    assert not missing, (
        f"parsers.unified.SessionStatistics fields without a "
        f"models.Statistics column: {sorted(missing)}"
    )


def test_service_statistics_fields_subset_of_orm_columns() -> None:
    """Every services-layer statistics field must exist as an ORM column."""
    missing = set(ServiceSessionStatistics.model_fields) - _statistics_column_names()
    assert not missing, (
        f"services.schemas.SessionStatistics fields without a "
        f"models.Statistics column: {sorted(missing)}"
    )


def test_orm_columns_sourced_from_unified_statistics() -> None:
    """Every ORM column must be populated from the unified model on import.

    Reverse of the subset check: an ORM column added without a matching unified
    field would never be written by the importer. Such a column must either get
    a unified field or be added to ``_IMPORTER_SUPPLIED_COLUMNS`` on purpose.
    """
    unsourced = (
        _statistics_column_names()
        - set(UnifiedSessionStatistics.model_fields)
        - _IMPORTER_SUPPLIED_COLUMNS
    )
    assert not unsourced, (
        f"models.Statistics columns not sourced from "
        f"parsers.unified.SessionStatistics (never imported): {sorted(unsourced)}"
    )
