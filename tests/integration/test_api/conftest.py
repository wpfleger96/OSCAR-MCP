import pytest

from starlette.types import ASGIApp, Receive, Scope, Send

import snore.api.import_jobs as _import_job_store

from snore.api.app import create_app
from snore.api.deps import get_actor, get_db


@pytest.fixture(autouse=True)
def reset_import_job_store():
    """Stop any running import worker and reset all import job globals before/after each test.

    The import worker is module-global.  Any test that starts one (via the
    ``import_worker`` fixture) or that accidentally triggers a worker-start must
    have the state cleaned up so subsequent tests start fresh.
    """
    # Stop any worker left over from a previous test BEFORE clearing globals.
    # Nulling without stopping first lets a still-running thread become a zombie.
    _import_job_store.stop_import_worker()
    _import_job_store._jobs.clear()
    _import_job_store._per_user_count.clear()
    _import_job_store._global_count = 0
    _import_job_store._import_queue.clear()
    yield
    # Teardown after the test (5 s to survive slow CI).
    _import_job_store.stop_import_worker(timeout=5.0)
    _import_job_store._jobs.clear()
    _import_job_store._per_user_count.clear()
    _import_job_store._global_count = 0
    _import_job_store._import_queue.clear()


@pytest.fixture
def import_worker():
    """Start the import worker with the real _run_import callback for this test.

    Use this fixture in tests that exercise the full upload-then-execute flow
    (SSE progress streaming, backup=True verification, etc.).  The
    ``reset_import_job_store`` autouse fixture handles teardown.
    """
    from snore.api.import_jobs import start_import_worker  # noqa: PLC0415
    from snore.api.routers.import_data import _run_import  # noqa: PLC0415

    start_import_worker(_run_import)


@pytest.fixture
def db_session(temp_db, async_db_session):
    """Sync seed-only session for seeding test data.

    Intentionally uses a sync AUTOCOMMIT session — this is a test-data seed
    helper, NOT an application code path.  Every ``flush()`` is immediately
    visible to the ``async_db_session`` used by the API override below.

    Do NOT use this fixture for new application-facing tests; use the async
    fixtures or ``real_app`` with ``httpx.AsyncClient`` instead.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from snore.database.models import Base

    engine = create_engine(
        f"sqlite:///{temp_db}",
        connect_args={"check_same_thread": False},
        isolation_level="AUTOCOMMIT",
    )
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def api_client(temp_db, async_db_session, db_session):
    """TestClient with get_db and get_actor overridden for test isolation.

    - ``get_db`` wraps the shared ``async_db_session`` in ``begin()`` so route
      handlers see the same commit/rollback semantics as production.
    - ``get_actor`` is overridden to provision a local admin actor from the same
      session, bypassing ``AuthMiddleware`` (which tries to open a second DB
      connection that isn't initialised in the test).  It declares
      ``Depends(get_db)`` — **not** ``Depends(override_get_db)`` directly — so
      that FastAPI's per-request dep cache resolves both usages to the same
      ``override_get_db`` node and calls ``begin()`` exactly once.

    Seeds are injected via the AUTOCOMMIT ``db_session`` so they are
    immediately visible inside the override session.
    """
    from typing import Annotated  # noqa: PLC0415

    from fastapi import Depends  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

    from snore.auth.actor import ActorContext, AuthMode  # noqa: PLC0415
    from snore.auth.factory import ActorContextFactory  # noqa: PLC0415

    app = create_app()

    async def override_get_db():
        async with async_db_session.begin():
            yield async_db_session

    # Declare Depends(get_db), not Depends(override_get_db):
    # FastAPI resolves get_db through the override table, so both
    # service_dep's get_db and this Depends(get_db) hit the same cache
    # node — begin() is called only once per request.
    async def override_get_actor(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> ActorContext:
        factory = ActorContextFactory(db)
        return await factory.make_local(mode=AuthMode.LOCAL)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_actor] = override_get_actor
    # Do NOT use 'with TestClient(app)' — that runs lifespan (init_database) which we
    # skip since we are overriding get_db entirely; lifespan tests use real_app instead.
    from fastapi.testclient import TestClient  # noqa: PLC0415

    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def localhost_api_client(temp_db, async_db_session, db_session):
    """TestClient that appears to connect from 127.0.0.1 (for localhost-only endpoints)."""
    from typing import Annotated  # noqa: PLC0415

    from fastapi import Depends  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

    from snore.auth.actor import (
        ActorContext,  # noqa: PLC0415
        AuthMode,  # noqa: PLC0415
    )
    from snore.auth.factory import ActorContextFactory  # noqa: PLC0415

    app = create_app()

    async def override_get_db():
        async with async_db_session.begin():
            yield async_db_session

    async def override_get_actor(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> ActorContext:
        factory = ActorContextFactory(db)
        return await factory.make_local(mode=AuthMode.LOCAL)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_actor] = override_get_actor

    class LocalhostMiddleware:
        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                scope["client"] = ("127.0.0.1", 12345)
            await self.app(scope, receive, send)

    wrapped = LocalhostMiddleware(app)
    from fastapi.testclient import TestClient  # noqa: PLC0415

    client = TestClient(wrapped, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()
