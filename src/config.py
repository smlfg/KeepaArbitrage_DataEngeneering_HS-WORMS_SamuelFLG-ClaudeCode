from functools import lru_cache
from typing import Optional
import os
from pathlib import Path

# Support environments without pydantic-settings (offline CI)
try:
    from pydantic_settings import BaseSettings  # type: ignore
except Exception:  # pragma: no cover - lightweight fallback
    try:
        from pydantic import BaseSettings  # type: ignore
    except Exception:

        class BaseSettings:  # minimal stub
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

            class Config:
                env_file = ".env"


class Settings(BaseSettings):
    app_name: str = "Keeper System"
    debug: bool = False
    log_level: str = "INFO"

    keepa_api_key: str = ""
    deal_source_mode: str = "product_only"
    deal_seed_asins: str = ""
    deal_seed_file: str = "data/seed_asins_eu_qwertz.txt"
    deal_targets_file: str = "data/seed_targets_eu_qwertz.csv"
    deal_scan_interval_seconds: int = 3600
    deal_scan_batch_size: int = 10

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/keeper"

    # Kafka Configuration
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_prices: str = "price-updates"
    kafka_topic_deals: str = "deal-updates"
    kafka_consumer_group: str = "keeper-consumer-group"

    # Elasticsearch Configuration
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index_prices: str = "keeper-prices"
    elasticsearch_index_deals: str = "keeper-deals"

    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None

    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    discord_webhook: Optional[str] = None

    class Config:
        env_file = str(Path(__file__).parent.parent / ".env")
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def get_keepa_api_key() -> str:
    return get_settings().keepa_api_key
