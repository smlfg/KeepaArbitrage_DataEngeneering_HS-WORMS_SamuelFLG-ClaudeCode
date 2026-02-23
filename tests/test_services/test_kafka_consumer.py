"""
Tests for Kafka Consumer classes

Covers: PriceUpdateConsumer.process_message, DealUpdateConsumer.process_message
- Unknown ASIN handling
- Price history save
- Alert creation when price <= target*1.01
- No duplicate alerts
- DB error handling
- DealUpdateConsumer: zero price, missing ASIN, record_deal_price call
"""

import pytest
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.kafka_consumer import PriceUpdateConsumer, DealUpdateConsumer


def _make_session_factory(session_mock):
    """Create an async context manager factory that yields the mock session."""
    @asynccontextmanager
    async def factory():
        yield session_mock
    return factory


# =============================================================================
# PriceUpdateConsumer Tests
# =============================================================================


class TestPriceUpdateConsumer:
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()
        return session

    @pytest.fixture
    def consumer(self, mock_session):
        return PriceUpdateConsumer(
            db_session_factory=_make_session_factory(mock_session)
        )

    @pytest.mark.asyncio
    async def test_process_known_asin_saves_price(self, consumer, mock_session):
        product = MagicMock()
        product.id = uuid.uuid4()
        product.asin = "B07W6JN8V8"
        product.target_price = 35.00

        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = product
        mock_session.execute = AsyncMock(return_value=result_mock)

        message = {
            "asin": "B07W6JN8V8",
            "current_price": 39.99,
            "target_price": 35.00,
        }

        result = await consumer.process_message(message)

        assert result is True
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_unknown_asin_returns_true(self, consumer, mock_session):
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=result_mock)

        message = {"asin": "UNKNOWN123", "current_price": 10.0, "target_price": None}

        result = await consumer.process_message(message)

        assert result is True

    @pytest.mark.asyncio
    async def test_creates_alert_when_price_at_target(self, consumer, mock_session):
        product = MagicMock()
        product.id = uuid.uuid4()
        product.asin = "B07W6JN8V8"
        product.target_price = 40.00

        # First execute returns product, second returns no existing alert
        no_alert_result = MagicMock()
        no_alert_result.scalars.return_value.first.return_value = None

        product_result = MagicMock()
        product_result.scalars.return_value.first.return_value = product

        mock_session.execute = AsyncMock(
            side_effect=[product_result, no_alert_result]
        )

        message = {
            "asin": "B07W6JN8V8",
            "current_price": 40.00,  # exactly at target * 1.01 boundary
            "target_price": 40.00,
        }

        result = await consumer.process_message(message)

        assert result is True
        # session.add called for price_history AND alert
        assert mock_session.add.call_count >= 2

    @pytest.mark.asyncio
    async def test_no_duplicate_alert(self, consumer, mock_session):
        product = MagicMock()
        product.id = uuid.uuid4()
        product.asin = "B07W6JN8V8"
        product.target_price = 40.00

        # Existing pending alert found
        existing_alert = MagicMock()
        existing_alert_result = MagicMock()
        existing_alert_result.scalars.return_value.first.return_value = existing_alert

        product_result = MagicMock()
        product_result.scalars.return_value.first.return_value = product

        mock_session.execute = AsyncMock(
            side_effect=[product_result, existing_alert_result]
        )

        message = {"asin": "B07W6JN8V8", "current_price": 35.0, "target_price": 40.0}

        result = await consumer.process_message(message)

        assert result is True
        # Only price_history added, NOT a second alert
        assert mock_session.add.call_count == 1

    @pytest.mark.asyncio
    async def test_db_error_returns_false(self, consumer, mock_session):
        mock_session.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        message = {"asin": "B123", "current_price": 10.0, "target_price": None}

        result = await consumer.process_message(message)

        assert result is False


# =============================================================================
# DealUpdateConsumer Tests
# =============================================================================


class TestDealUpdateConsumer:
    @pytest.mark.asyncio
    async def test_process_valid_deal(self):
        consumer = DealUpdateConsumer()

        with patch("src.services.database.record_deal_price", new_callable=AsyncMock) as mock_record:
            mock_record.return_value = True

            message = {
                "asin": "B07W6JN8V8",
                "current_price": 39.99,
                "product_title": "Test Keyboard",
            }

            result = await consumer.process_message(message)

            assert result is True
            mock_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_zero_price_returns_false(self):
        consumer = DealUpdateConsumer()

        message = {"asin": "B07W6JN8V8", "current_price": 0, "product_title": "Test"}

        # Zero price should be caught before record_deal_price is called
        result = await consumer.process_message(message)

        assert result is False

    @pytest.mark.asyncio
    async def test_process_missing_asin_returns_false(self):
        consumer = DealUpdateConsumer()

        message = {"asin": "", "current_price": 39.99, "product_title": "Test"}

        result = await consumer.process_message(message)

        assert result is False

    @pytest.mark.asyncio
    async def test_process_exception_returns_false(self):
        consumer = DealUpdateConsumer()

        with patch(
            "src.services.database.record_deal_price",
            new_callable=AsyncMock,
            side_effect=Exception("DB error"),
        ):
            message = {
                "asin": "B07W6JN8V8",
                "current_price": 39.99,
                "product_title": "Test",
            }

            result = await consumer.process_message(message)

            assert result is False
