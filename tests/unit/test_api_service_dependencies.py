"""Transaction-mode wiring for profile-scoped API services."""

import typing

from collections.abc import Callable

import pytest

from fastapi import params

from snore.api.deps import get_db, get_db_immediate
from snore.api.routers.analysis import (
    delete_analysis,
    get_analysis,
    get_analysis_delete_preview,
    list_analysis_sessions,
    run_analysis,
    run_batch_analysis,
)
from snore.api.routers.equipment import (
    create_mask_log_entry,
    delete_mask_log_entry,
    list_mask_epochs,
    list_mask_log_entries,
    update_mask_log_entry,
)
from snore.api.routers.sessions import (
    bulk_delete_preview,
    delete_sessions,
    get_delete_preview,
    get_session,
    list_sessions,
    update_session,
)


def _service_db_dependency(
    route: Callable[..., object], service_parameter: str
) -> Callable[..., object]:
    """Return the DB dependency nested inside a route's service dependency."""
    route_hints = typing.get_type_hints(route, include_extras=True)
    service_annotation = route_hints[service_parameter]
    service_dep = next(
        value
        for value in typing.get_args(service_annotation)
        if isinstance(value, params.Depends)
    )
    assert service_dep.dependency is not None

    dependency_hints = typing.get_type_hints(
        service_dep.dependency, include_extras=True
    )
    db_annotation = dependency_hints["db"]
    db_dep = next(
        value
        for value in typing.get_args(db_annotation)
        if isinstance(value, params.Depends)
    )
    assert db_dep.dependency is not None
    return db_dep.dependency


@pytest.mark.parametrize(
    ("route", "service_parameter"),
    [
        (update_session, "service"),
        (delete_sessions, "service"),
        (delete_analysis, "facade"),
        (update_mask_log_entry, "service"),
        (delete_mask_log_entry, "service"),
    ],
)
def test_read_then_write_service_routes_use_immediate_transactions(
    route: Callable[..., object], service_parameter: str
) -> None:
    assert _service_db_dependency(route, service_parameter) is get_db_immediate


@pytest.mark.parametrize(
    ("route", "service_parameter"),
    [
        (list_sessions, "service"),
        (bulk_delete_preview, "service"),
        (get_delete_preview, "service"),
        (get_session, "service"),
        (list_analysis_sessions, "facade"),
        (get_analysis, "facade"),
        (get_analysis_delete_preview, "facade"),
        (run_analysis, "facade"),
        (run_batch_analysis, "facade"),
        (list_mask_epochs, "service"),
        (list_mask_log_entries, "service"),
        (create_mask_log_entry, "service"),
    ],
)
def test_non_upgrading_service_routes_keep_deferred_transactions(
    route: Callable[..., object], service_parameter: str
) -> None:
    assert _service_db_dependency(route, service_parameter) is get_db
