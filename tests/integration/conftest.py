import asyncio

import pytest

from snore.database.session import cleanup_database


@pytest.fixture(autouse=True)
def reset_database_state():
    """Reset global database state before and after each test."""
    asyncio.run(cleanup_database())
    yield
    asyncio.run(cleanup_database())
