import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("KEEPA_API_KEY", "test_dummy_key_for_ci")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch

from src.api.main import app
from src.services.database import init_db


@pytest_asyncio.fixture
async def client(mock_keepa):
    # Initialize the database before running tests
    await init_db()

    with patch("src.api.main.get_keepa_client", return_value=mock_keepa):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
def mock_keepa():
    mock = MagicMock()
    mock.query_product = AsyncMock(return_value=None)
    mock.search_deals = AsyncMock(return_value=[])
    mock.ensure_initialized = AsyncMock()
    # These are called synchronously (not awaited) in the endpoints
    mock.get_token_status.return_value = {"tokens_available": 1000}
    mock.check_rate_limit.return_value = {"status": "ok", "tokens_available": 1000}
    return mock
