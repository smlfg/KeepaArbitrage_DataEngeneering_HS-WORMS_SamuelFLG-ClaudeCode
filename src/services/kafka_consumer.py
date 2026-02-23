import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.services.database import PriceHistory, WatchedProduct

try:
    from src.utils.pipeline_logger import log_kafka_consume
    _PIPELINE_LOG = True
except ImportError:
    _PIPELINE_LOG = False

logger = logging.getLogger(__name__)
settings = get_settings()


MAX_RECONNECT_ATTEMPTS = 10
MIN_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300


class PriceUpdateConsumer:
    def __init__(self, db_session_factory):
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.topic = settings.kafka_topic_prices
        self.group_id = settings.kafka_consumer_group
        self.db_session_factory = db_session_factory
        self.running = False

    async def start(self):
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=True,
        )
        await self.consumer.start()
        self.running = True
        logger.info(f"Kafka price consumer started - topic: {self.topic}")

    async def stop(self):
        self.running = False
        if self.consumer:
            await self.consumer.stop()
            logger.info("Kafka price consumer stopped")

    async def process_message(self, message: Dict[str, Any]) -> bool:
        try:
            async with self.db_session_factory() as session:
                asin = message.get("asin")
                current_price = message.get("current_price")
                target_price = message.get("target_price")

                result = await session.execute(
                    select(WatchedProduct).where(WatchedProduct.asin == asin)
                )
                product = result.scalars().first()

                if product and current_price is not None:
                    await self._save_price_history(session, product.id, current_price)

                    if target_price and current_price <= target_price * 1.01:
                        await self._create_alert(session, product, current_price)

                    await session.commit()

                return True
        except Exception as e:
            logger.error(f"Error processing price message: {e}")
            return False

    async def _save_price_history(self, session: AsyncSession, watch_id, price: float):
        price_record = PriceHistory(
            watch_id=watch_id,
            price=price,
        )
        session.add(price_record)

    async def _create_alert(
        self, session: AsyncSession, product: WatchedProduct, current_price: float
    ):
        from src.services.database import PriceAlert, AlertStatus

        existing_alert = await session.execute(
            select(PriceAlert).where(
                PriceAlert.watch_id == product.id,
                PriceAlert.status == AlertStatus.PENDING,
            )
        )

        if not existing_alert.scalars().first():
            alert = PriceAlert(
                watch_id=product.id,
                target_price=product.target_price,
                triggered_price=current_price,
                status=AlertStatus.PENDING,
            )
            session.add(alert)
            logger.info(f"Created price alert for product {product.asin}")

    async def consume(self):
        consecutive_errors = 0
        while self.running:
            try:
                _batch_count = 0
                _batch_start = time.time()
                async for message in self.consumer:
                    if not self.running:
                        break
                    await self.process_message(message.value)
                    _batch_count += 1
                    consecutive_errors = 0  # Reset on successful processing
                    if _PIPELINE_LOG and _batch_count % 10 == 0:
                        log_kafka_consume(messages_processed=_batch_count, duration_ms=(time.time() - _batch_start) * 1000)
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(f"Price consumer giving up after {MAX_RECONNECT_ATTEMPTS} consecutive errors: {e}")
                    self.running = False
                    break
                backoff = min(MIN_BACKOFF_SECONDS * (2 ** (consecutive_errors - 1)), MAX_BACKOFF_SECONDS)
                logger.error(f"Price consumer error ({consecutive_errors}/{MAX_RECONNECT_ATTEMPTS}), retrying in {backoff}s: {e}")
                if self.running:
                    await asyncio.sleep(backoff)


class DealUpdateConsumer:
    def __init__(self, db_session_factory=None):
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.topic = settings.kafka_topic_deals
        self.group_id = f"{settings.kafka_consumer_group}-deals"
        self.db_session_factory = db_session_factory
        self.running = False

    async def start(self):
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="earliest",
        )
        await self.consumer.start()
        self.running = True
        logger.info(f"Kafka deal consumer started - topic: {self.topic}")

    async def stop(self):
        self.running = False
        if self.consumer:
            await self.consumer.stop()

    async def process_message(self, message: Dict[str, Any]) -> bool:
        """Process a deal message: record price in price_history."""
        try:
            from src.services.database import record_deal_price

            asin = message.get("asin", "")
            price = message.get("current_price", 0)
            title = message.get("product_title", "") or message.get("title", "")

            if asin and price > 0:
                success = await record_deal_price(
                    asin=asin,
                    price=price,
                    title=title,
                    source="kafka_deals",
                )
                if success:
                    logger.debug(f"Recorded deal price: {asin} @ {price}")
                return success
            return False
        except Exception as e:
            logger.error(f"Error processing deal message: {e}")
            return False

    async def consume(self):
        consecutive_errors = 0
        while self.running:
            try:
                _batch_count = 0
                _batch_start = time.time()
                async for message in self.consumer:
                    if not self.running:
                        break
                    await self.process_message(message.value)
                    _batch_count += 1
                    consecutive_errors = 0  # Reset on successful processing
                    if _PIPELINE_LOG and _batch_count % 10 == 0:
                        log_kafka_consume(messages_processed=_batch_count, duration_ms=(time.time() - _batch_start) * 1000)
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(f"Deal consumer giving up after {MAX_RECONNECT_ATTEMPTS} consecutive errors: {e}")
                    self.running = False
                    break
                backoff = min(MIN_BACKOFF_SECONDS * (2 ** (consecutive_errors - 1)), MAX_BACKOFF_SECONDS)
                logger.error(f"Deal consumer error ({consecutive_errors}/{MAX_RECONNECT_ATTEMPTS}), retrying in {backoff}s: {e}")
                if self.running:
                    await asyncio.sleep(backoff)
