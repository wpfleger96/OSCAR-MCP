import pytest

from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from snore.api.app import create_app
from snore.api.deps import get_db


@pytest.fixture
def db_session(temp_db, async_db_session):
    """Sync session for seeding test data.

    Uses ``AUTOCOMMIT`` isolation so every ``flush()`` is immediately visible
    to the ``async_db_session`` used by the API override below.  This avoids
    the need for explicit ``commit()`` calls in test bodies.

    Both sessions point at the same ``temp_db`` file.
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
    """TestClient with get_db overridden to use the test async session."""
    app = create_app()

    async def override_get_db():
        yield async_db_session

    app.dependency_overrides[get_db] = override_get_db
    # Do NOT use 'with TestClient(app)' — that runs lifespan (init_database) which we skip
    # since we're overriding get_db entirely
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def localhost_api_client(async_db_session, db_session):
    """TestClient that appears to connect from 127.0.0.1 (for localhost-only endpoints)."""
    app = create_app()

    async def override_get_db():
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
    client = TestClient(wrapped, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()
