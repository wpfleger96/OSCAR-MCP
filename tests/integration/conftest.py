import pytest

from snore.database.session import cleanup_database


@pytest.fixture(autouse=True)
def reset_database_state():
    """Reset global database state before and after each test."""
    cleanup_database()
    yield
    cleanup_database()
