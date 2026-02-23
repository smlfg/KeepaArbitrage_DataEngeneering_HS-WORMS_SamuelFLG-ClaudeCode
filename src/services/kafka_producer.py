import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from src.config import get_settings

try:
    from src.utils.pipeline_logger import log_kafka_produce
    _PIPELINE_LOG = True
except ImportError:
    _PIPELINE_LOG = False

logger = logging.getLogger(__name__)
settings = get_settings()


class PriceUpdateProducer:
    def __init__(self):
        self.producer: Optional[AIOKafkaProducer] = None
        self.topic = settings.kafka_topic_prices

    async def start(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self.producer.start()
        logger.info(
            f"Kafka producer started - connecting to {settings.kafka_bootstrap_servers}"
        )

    async def stop(self):
        if self.producer:
            await self.producer.stop()
            logger.info("Kafka producer stopped")

    async def send_price_update(
        self,
        asin: str,
        product_title: str,
        current_price: float,
        target_price: Optional[float],
        previous_price: Optional[float],
        domain: str = "de",
        currency: str = "EUR",
    ) -> bool:
        if not self.producer:
            logger.warning("Producer not initialized, cannot send message")
            return False

        message = {
            "asin": asin,
            "product_title": product_title,
            "current_price": current_price,
            "target_price": target_price,
            "previous_price": previous_price,
            "price_change": (
                round(((previous_price - current_price) / previous_price) * 100, 2)
                if previous_price and previous_price > 0
                else 0
            ),
            "domain": domain,
            "currency": currency,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "price_update",
        }

        try:
            result = await self.producer.send_and_wait(self.topic, key=asin, value=message)
            if _PIPELINE_LOG:
                log_kafka_produce(topic=self.topic, partition=result.partition, offset=result.offset)
            logger.debug(f"Sent price update for ASIN: {asin}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send price update for {asin}: {e}")
            return False

    async def send_batch_price_updates(
        self, price_updates: list[Dict[str, Any]]
    ) -> int:
        success_count = 0
        for update in price_updates:
            result = await self.send_price_update(
                asin=update.get("asin", ""),
                product_title=update.get("product_title", ""),
                current_price=update.get("current_price", 0),
                target_price=update.get("target_price"),
                previous_price=update.get("previous_price"),
                domain=update.get("domain", "de"),
                currency=update.get("currency", "EUR"),
            )
            if result:
                success_count += 1
        return success_count


class DealUpdateProducer:
    def __init__(self):
        self.producer: Optional[AIOKafkaProducer] = None
        self.topic = settings.kafka_topic_deals

    async def start(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        )
        await self.producer.start()
        logger.info(
            f"Deal producer started - connecting to {settings.kafka_bootstrap_servers}"
        )

    async def stop(self):
        if self.producer:
            await self.producer.stop()

    async def send_deal_update(
        self,
        asin: str,
        product_title: str,
        current_price: float,
        original_price: float,
        discount_percent: float,
        rating: float,
        review_count: int,
        sales_rank: Optional[int],
        domain: str = "de",
    ) -> bool:
        if not self.producer:
            return False

        message = {
            "asin": asin,
            "product_title": product_title,
            "current_price": current_price,
            "original_price": original_price,
            "discount_percent": discount_percent,
            "rating": rating,
            "review_count": review_count,
            "sales_rank": sales_rank,
            "domain": domain,
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "deal_update",
        }

        try:
            result = await self.producer.send_and_wait(self.topic, key=asin, value=message)
            if _PIPELINE_LOG:
                log_kafka_produce(topic=self.topic, partition=result.partition, offset=result.offset)
            logger.debug(f"Sent deal update for ASIN: {asin}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send deal update for {asin}: {e}")
            return False


price_producer = PriceUpdateProducer()
deal_producer = DealUpdateProducer()
