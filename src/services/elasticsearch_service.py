import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from elasticsearch import AsyncElasticsearch

from src.config import get_settings

try:
    from src.utils.pipeline_logger import log_es_index
    _PIPELINE_LOG = True
except ImportError:
    _PIPELINE_LOG = False

logger = logging.getLogger(__name__)
settings = get_settings()


PRICE_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "asin": {"type": "keyword"},
            "product_title": {
                "type": "text",
                "analyzer": "standard",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "current_price": {"type": "float"},
            "target_price": {"type": "float"},
            "previous_price": {"type": "float"},
            "price_change_percent": {"type": "float"},
            "domain": {"type": "keyword"},
            "currency": {"type": "keyword"},
            "timestamp": {"type": "date"},
            "event_type": {"type": "keyword"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "index": {
            "max_result_window": 50000,
        },
    },
}

DEAL_INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "deal_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "german_stemmer", "asciifolding"],
                }
            },
            "filter": {"german_stemmer": {"type": "stemmer", "language": "german"}},
        },
    },
    "mappings": {
        "properties": {
            "asin": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "deal_analyzer",
                "fields": {
                    "keyword": {"type": "keyword"},
                    "suggest": {"type": "completion"},
                },
            },
            "description": {"type": "text", "analyzer": "deal_analyzer"},
            "current_price": {"type": "float"},
            "original_price": {"type": "float"},
            "discount_percent": {"type": "float"},
            "rating": {"type": "float"},
            "review_count": {"type": "integer"},
            "sales_rank": {"type": "integer"},
            "domain": {"type": "keyword"},
            "category": {"type": "keyword"},
            "prime_eligible": {"type": "boolean"},
            "url": {"type": "keyword"},
            "deal_score": {"type": "float"},
            "timestamp": {"type": "date"},
            "event_type": {"type": "keyword"},
        }
    },
}


METRICS_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"},
            "operation": {"type": "keyword"},
            "tokens_consumed": {"type": "integer"},
            "tokens_left": {"type": "integer"},
            "refill_rate": {"type": "integer"},
            "refill_in": {"type": "integer"},
            "response_time_ms": {"type": "integer"},
            "asin_count": {"type": "integer"},
            "domain": {"type": "keyword"},
            "success": {"type": "boolean"},
            "error": {"type": "text"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}


class ElasticsearchService:
    def __init__(self):
        self.client: Optional[AsyncElasticsearch] = None
        self.prices_index = settings.elasticsearch_index_prices
        self.deals_index = settings.elasticsearch_index_deals
        self.metrics_index = "keeper-metrics"

    async def connect(self):
        self.client = AsyncElasticsearch([settings.elasticsearch_url])
        await self._create_indices()
        logger.info(f"Connected to Elasticsearch at {settings.elasticsearch_url}")

    async def close(self):
        if self.client:
            await self.client.close()
            logger.info("Elasticsearch connection closed")

    async def _create_indices(self):
        if not self.client:
            return

        for index_name, mapping in [
            (self.prices_index, PRICE_INDEX_MAPPING),
            (self.deals_index, DEAL_INDEX_MAPPING),
            (self.metrics_index, METRICS_INDEX_MAPPING),
        ]:
            try:
                if not await self.client.indices.exists(index=index_name):
                    await self.client.indices.create(index=index_name, body=mapping)
                    logger.info(f"Created Elasticsearch index: {index_name}")
            except Exception as e:
                logger.error(f"Error creating index {index_name}: {e}")

    async def _index_with_retry(self, index: str, document: Dict[str, Any], max_retries: int = 3) -> bool:
        """Index a document with exponential backoff retry (1s, 2s, 4s)."""
        for attempt in range(max_retries):
            try:
                await self.client.index(index=index, document=document)
                if _PIPELINE_LOG:
                    log_es_index(docs_indexed=1)
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"ES index retry {attempt + 1}/{max_retries} for {index}: {e} (waiting {wait}s)")
                    await asyncio.sleep(wait)
                else:
                    if _PIPELINE_LOG:
                        log_es_index(docs_indexed=0, errors=[str(e)])
                    logger.error(f"ES index failed after {max_retries} retries for {index}: {e}")
                    return False

    async def index_price_update(self, price_data: Dict[str, Any]) -> bool:
        if not self.client:
            return False
        return await self._index_with_retry(self.prices_index, price_data)

    async def index_deal_update(self, deal_data: Dict[str, Any]) -> bool:
        if not self.client:
            return False
        return await self._index_with_retry(self.deals_index, deal_data)

    async def index_token_metric(self, metric: Dict[str, Any]) -> bool:
        if not self.client:
            return False
        if "timestamp" not in metric:
            metric["timestamp"] = datetime.utcnow().isoformat()
        try:
            await self.client.index(index=self.metrics_index, document=metric)
            return True
        except Exception as e:
            logger.debug(f"Token metric index failed: {e}")
            return False

    async def search_prices(
        self,
        asin: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        domain: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page: int = 0,
        size: int = 20,
    ) -> Dict[str, Any]:
        if not self.client:
            return {"hits": []}

        must_clauses = []

        if asin:
            must_clauses.append({"term": {"asin": asin}})

        if min_price or max_price:
            price_range = {"range": {"current_price": {}}}
            if min_price:
                price_range["range"]["current_price"]["gte"] = min_price
            if max_price:
                price_range["range"]["current_price"]["lte"] = max_price
            must_clauses.append(price_range)

        if domain:
            must_clauses.append({"term": {"domain": domain}})

        if from_date or to_date:
            date_range = {"range": {"timestamp": {}}}
            if from_date:
                date_range["range"]["timestamp"]["gte"] = from_date.isoformat()
            if to_date:
                date_range["range"]["timestamp"]["lte"] = to_date.isoformat()
            must_clauses.append(date_range)

        query = {"bool": {"must": must_clauses}} if must_clauses else {"match_all": {}}

        try:
            result = await self.client.search(
                index=self.prices_index,
                query=query,
                from_=page * size,
                size=size,
                sort=[{"timestamp": "desc"}],
            )
            return result
        except Exception as e:
            logger.error(f"Error searching prices: {e}")
            return {"hits": []}

    async def get_price_statistics(self, asin: str) -> Dict[str, Any]:
        if not self.client:
            return {}

        query = {"term": {"asin": asin}}

        try:
            result = await self.client.search(
                index=self.prices_index,
                query=query,
                size=0,
                aggs={
                    "price_stats": {"stats": {"field": "current_price"}},
                    "price_changes": {
                        "histogram": {
                            "field": "current_price",
                            "interval": 10,
                        }
                    },
                },
            )
            return result.get("aggregations", {})
        except Exception as e:
            logger.error(f"Error getting price statistics: {e}")
            return {}

    async def get_deal_aggregations(
        self,
        min_discount: float = 0,
        min_rating: float = 0,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.client:
            return {}

        must_clauses = [
            {"range": {"discount_percent": {"gte": min_discount}}},
            {"range": {"rating": {"gte": min_rating}}},
        ]

        if domain:
            must_clauses.append({"term": {"domain": domain}})

        query = {"bool": {"must": must_clauses}}

        try:
            result = await self.client.search(
                index=self.deals_index,
                query=query,
                size=0,
                aggs={
                    "by_discount": {"terms": {"field": "discount_percent", "size": 10}},
                    "by_domain": {"terms": {"field": "domain"}},
                    "avg_price": {"avg": {"field": "current_price"}},
                    "avg_discount": {"avg": {"field": "discount_percent"}},
                },
            )
            return result.get("aggregations", {})
        except Exception as e:
            logger.error(f"Error getting deal aggregations: {e}")
            return {}

    async def get_deal_price_stats(self, asin: str) -> Dict[str, Any]:
        """Get price statistics for an ASIN from deal snapshots in ES."""
        if not self.client:
            return {}

        query = {"term": {"asin": asin}}

        try:
            result = await self.client.search(
                index=self.deals_index,
                query=query,
                size=0,
                aggs={
                    "price_stats": {"stats": {"field": "current_price"}},
                    "latest_price": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"timestamp": "desc"}],
                            "_source": ["current_price", "timestamp"],
                        }
                    },
                    "price_over_time": {
                        "date_histogram": {
                            "field": "timestamp",
                            "calendar_interval": "day",
                        },
                        "aggs": {
                            "avg_price": {"avg": {"field": "current_price"}},
                            "min_price": {"min": {"field": "current_price"}},
                            "max_price": {"max": {"field": "current_price"}},
                        },
                    },
                },
            )

            aggs = result.get("aggregations", {})
            stats = aggs.get("price_stats", {})
            latest_hits = aggs.get("latest_price", {}).get("hits", {}).get("hits", [])
            current = (
                latest_hits[0]["_source"]["current_price"] if latest_hits else None
            )

            return {
                "min": stats.get("min"),
                "max": stats.get("max"),
                "avg": round(stats.get("avg", 0), 2) if stats.get("avg") else None,
                "current": current,
                "data_points": stats.get("count", 0),
                "price_over_time": [
                    {
                        "date": bucket["key_as_string"],
                        "avg_price": round(bucket["avg_price"]["value"], 2)
                        if bucket["avg_price"]["value"]
                        else None,
                        "min_price": bucket["min_price"]["value"],
                        "max_price": bucket["max_price"]["value"],
                    }
                    for bucket in aggs.get("price_over_time", {}).get("buckets", [])
                ],
            }
        except Exception as e:
            logger.error(f"Error getting deal price stats for {asin}: {e}")
            return {}

    async def delete_old_data(self, days: int = 90) -> int:
        if not self.client:
            return 0

        from datetime import timedelta

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        try:
            result = await self.client.delete_by_query(
                index=f"{self.prices_index},{self.deals_index}",
                query={"range": {"timestamp": {"lt": cutoff_date.isoformat()}}},
            )
            deleted = result.get("deleted", 0)
            logger.info(f"Deleted {deleted} old documents")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting old data: {e}")
            return 0


es_service = ElasticsearchService()
