"""
Pytest configuration and fixtures for Keeper System tests.
Handles SQLite compatibility for PostgreSQL UUID types.
"""

import os
import sys
import uuid

# Set env vars BEFORE any src import
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("KEEPA_API_KEY", "test_dummy_key_for_ci")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# Monkey-patch PostgreSQL UUID for SQLite compatibility BEFORE importing database models
from sqlalchemy import String, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID as PostgresqlUUID


class SQLiteUUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL UUID type when available, otherwise stores as String(36).
    This allows tests to run on SQLite while production uses PostgreSQL.
    """

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresqlUUID(as_uuid=True))
        else:
            return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# Monkey-patch the UUID import in sqlalchemy.dialects.postgresql
# so database.py's `UUID(as_uuid=True)` resolves to our SQLiteUUID
import sqlalchemy.dialects.postgresql as pg

pg.UUID = lambda **kwargs: SQLiteUUID()

from unittest.mock import AsyncMock, MagicMock, patch

# Clear lru_cache so settings picks up the test env vars
from src.config import get_settings

get_settings.cache_clear()

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.services.database import Base

import pytest
import pytest_asyncio


# Session-scoped async engine using SQLite in-memory
@pytest.fixture(scope="session")
def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return eng


@pytest_asyncio.fixture(scope="session")
async def db_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(engine, db_tables):
    AsyncSessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def patch_database_engine(engine):
    """Patch database module to use test engine for all operations."""
    from src.services import database

    # Store original values
    original_engine = database.engine
    original_session_maker = database.async_session_maker

    # Create new session maker with test engine
    test_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Patch the module
    database.engine = engine
    database.async_session_maker = test_session_maker

    yield

    # Restore original values
    database.engine = original_engine
    database.async_session_maker = original_session_maker


@pytest_asyncio.fixture(autouse=True)
async def cleanup_tables(engine, request):
    """Clean up all tables after each test to ensure isolation."""
    yield
    # Skip cleanup for agent tests that don't use the database
    if "test_agents" in request.node.nodeid:
        return
    # Clean up all tables after each test
    from src.services.database import Base
    from sqlalchemy.exc import OperationalError

    try:
        async with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
    except OperationalError:
        # Tables may not exist (e.g., in-memory database not initialized)
        pass


@pytest.fixture
def mock_keepa_client():
    client = MagicMock()
    client.query_product = AsyncMock(return_value=None)
    client.search_deals = AsyncMock(return_value=[])
    client.ensure_initialized = AsyncMock()
    client.token_status = MagicMock(
        tokens_available=1000, tokens_needed=lambda x: x <= 1000
    )
    return client


@pytest.fixture
def mock_notification():
    svc = MagicMock()
    svc.send_price_alert = AsyncMock()
    svc.send_deal_report = AsyncMock()
    return svc
