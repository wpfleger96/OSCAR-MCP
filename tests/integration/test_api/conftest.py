import pytest

from starlette.types import ASGIApp, Receive, Scope, Send

from snore.api.app import create_app
from snore.api.deps import get_db


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
def api_client(async_db_session, db_session):
    """TestClient with get_db overridden to use a transaction-equivalent async dependency.

    The override mirrors the real ``get_db`` dependency: it wraps the session in
    ``async with session.begin()`` so route handlers see the same commit/rollback
    semantics as production.  Seeds are injected via the AUTOCOMMIT ``db_session``
    so they are immediately visible inside the override session.
    """
    app = create_app()

    async def override_get_db():
        async with async_db_session.begin():
            yield async_db_session

    app.dependency_overrides[get_db] = override_get_db
    # Do NOT use 'with TestClient(app)' — that runs lifespan (init_database) which we
    # skip since we are overriding get_db entirely; lifespan tests use real_app instead.
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def localhost_api_client(async_db_session, db_session):
    """TestClient that appears to connect from 127.0.0.1 (for localhost-only endpoints)."""
    app = create_app()

    async def override_get_db():
        async with async_db_session.begin():
            yield async_db_session

    app.dependency_overrides[get_db] = override_get_db

    class LocalhostMiddleware:
        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                scope["client"] = ("127.0.0.1", 12345)
            await self.app(scope, receive, send)

    wrapped = LocalhostMiddleware(app)
    from fastapi.testclient import TestClient

    client = TestClient(wrapped, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()
