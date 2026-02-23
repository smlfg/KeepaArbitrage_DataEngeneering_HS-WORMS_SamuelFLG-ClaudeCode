"""
Tests for Kafka Producer classes

Covers: PriceUpdateProducer, DealUpdateProducer
- start/stop lifecycle
- send_price_update: success, no-producer, KafkaError
- send_deal_update: success, no-producer, KafkaError
- send_batch_price_updates
- message field validation, price_change calculation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiokafka.errors import KafkaError

from src.services.kafka_producer import PriceUpdateProducer, DealUpdateProducer


# =============================================================================
# PriceUpdateProducer Tests
# =============================================================================


class TestPriceUpdateProducer:
    @pytest.fixture
    def producer(self):
        p = PriceUpdateProducer()
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(
            return_value=MagicMock(partition=0, offset=1)
        )
        p.producer = mock_producer
        return p

    @pytest.fixture
    def producer_no_init(self):
        return PriceUpdateProducer()

    @pytest.mark.asyncio
    async def test_start_creates_producer(self):
        p = PriceUpdateProducer()
        with patch("src.services.kafka_producer.AIOKafkaProducer") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            await p.start()
            assert p.producer is mock_instance
            mock_instance.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_stops_producer(self, producer):
        await producer.stop()
        producer.producer.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_noop_when_no_producer(self, producer_no_init):
        await producer_no_init.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_send_price_update_success(self, producer):
        result = await producer.send_price_update(
            asin="B07W6JN8V8",
            product_title="Test Keyboard",
            current_price=39.99,
            target_price=35.00,
            previous_price=49.99,
        )

        assert result is True
        producer.producer.send_and_wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_price_update_returns_false_no_producer(self, producer_no_init):
        result = await producer_no_init.send_price_update(
            asin="B07W6JN8V8",
            product_title="Test",
            current_price=39.99,
            target_price=None,
            previous_price=None,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_send_price_update_returns_false_on_kafka_error(self, producer):
        producer.producer.send_and_wait = AsyncMock(side_effect=KafkaError("broker down"))

        result = await producer.send_price_update(
            asin="B07W6JN8V8",
            product_title="Test",
            current_price=39.99,
            target_price=None,
            previous_price=None,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_message_contains_required_fields(self, producer):
        await producer.send_price_update(
            asin="B07W6JN8V8",
            product_title="Test Keyboard",
            current_price=39.99,
            target_price=35.00,
            previous_price=49.99,
            domain="de",
            currency="EUR",
        )

        call_kwargs = producer.producer.send_and_wait.call_args
        message = call_kwargs.kwargs["value"]
        assert message["asin"] == "B07W6JN8V8"
        assert message["product_title"] == "Test Keyboard"
        assert message["current_price"] == 39.99
        assert message["event_type"] == "price_update"
        assert "timestamp" in message

    @pytest.mark.asyncio
    async def test_price_change_calculation(self, producer):
        await producer.send_price_update(
            asin="B123",
            product_title="Test",
            current_price=40.0,
            target_price=None,
            previous_price=50.0,
        )

        message = producer.producer.send_and_wait.call_args.kwargs["value"]
        assert message["price_change"] == 20.0  # (50-40)/50 * 100 = 20%

    @pytest.mark.asyncio
    async def test_price_change_zero_when_no_previous(self, producer):
        await producer.send_price_update(
            asin="B123",
            product_title="Test",
            current_price=40.0,
            target_price=None,
            previous_price=None,
        )

        message = producer.producer.send_and_wait.call_args.kwargs["value"]
        assert message["price_change"] == 0

    @pytest.mark.asyncio
    async def test_send_batch_counts_successes(self, producer):
        updates = [
            {"asin": "B001", "product_title": "KB1", "current_price": 30.0},
            {"asin": "B002", "product_title": "KB2", "current_price": 40.0},
            {"asin": "B003", "product_title": "KB3", "current_price": 50.0},
        ]

        count = await producer.send_batch_price_updates(updates)

        assert count == 3

    @pytest.mark.asyncio
    async def test_send_batch_partial_failure(self, producer):
        producer.producer.send_and_wait = AsyncMock(
            side_effect=[MagicMock(partition=0, offset=0), KafkaError("fail"), MagicMock(partition=0, offset=1)]
        )

        updates = [
            {"asin": "B001", "product_title": "KB1", "current_price": 30.0},
            {"asin": "B002", "product_title": "KB2", "current_price": 40.0},
            {"asin": "B003", "product_title": "KB3", "current_price": 50.0},
        ]

        count = await producer.send_batch_price_updates(updates)

        assert count == 2


# =============================================================================
# DealUpdateProducer Tests
# =============================================================================


class TestDealUpdateProducer:
    @pytest.fixture
    def producer(self):
        p = DealUpdateProducer()
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(
            return_value=MagicMock(partition=0, offset=1)
        )
        p.producer = mock_producer
        return p

    @pytest.mark.asyncio
    async def test_send_deal_update_success(self, producer):
        result = await producer.send_deal_update(
            asin="B07W6JN8V8",
            product_title="Test Keyboard",
            current_price=39.99,
            original_price=59.99,
            discount_percent=33.0,
            rating=4.5,
            review_count=1200,
            sales_rank=500,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_send_deal_update_returns_false_no_producer(self):
        p = DealUpdateProducer()
        result = await p.send_deal_update(
            asin="B123",
            product_title="Test",
            current_price=39.99,
            original_price=59.99,
            discount_percent=33.0,
            rating=4.5,
            review_count=100,
            sales_rank=None,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_send_deal_update_returns_false_on_error(self, producer):
        producer.producer.send_and_wait = AsyncMock(side_effect=KafkaError("fail"))

        result = await producer.send_deal_update(
            asin="B123",
            product_title="Test",
            current_price=39.99,
            original_price=59.99,
            discount_percent=33.0,
            rating=4.5,
            review_count=100,
            sales_rank=None,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_message_has_deal_event_type(self, producer):
        await producer.send_deal_update(
            asin="B123",
            product_title="Test",
            current_price=39.99,
            original_price=59.99,
            discount_percent=33.0,
            rating=4.5,
            review_count=100,
            sales_rank=500,
        )

        message = producer.producer.send_and_wait.call_args.kwargs["value"]
        assert message["event_type"] == "deal_update"
        assert message["discount_percent"] == 33.0
        assert message["sales_rank"] == 500
