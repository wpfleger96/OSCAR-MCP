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
