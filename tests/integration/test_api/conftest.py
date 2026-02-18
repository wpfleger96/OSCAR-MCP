import pytest

from fastapi.testclient import TestClient

from snore.api.app import create_app
from snore.api.deps import get_db


@pytest.fixture
def api_client(db_session):
    """TestClient with get_db overridden to use the test db_session fixture."""
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # Do NOT use 'with TestClient(app)' — that runs lifespan (init_database) which we skip
    # since we're overriding get_db entirely
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()
