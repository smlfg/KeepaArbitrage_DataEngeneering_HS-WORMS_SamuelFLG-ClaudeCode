"""
Tests for ElasticsearchService

Covers: connect, close, _create_indices, index_price_update, index_deal_update,
search_prices, get_price_statistics, get_deal_aggregations, get_deal_price_stats,
delete_old_data, _index_with_retry
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.services.elasticsearch_service import ElasticsearchService


@pytest.fixture
def es_service():
    """Fresh ElasticsearchService with mocked client."""
    svc = ElasticsearchService()
    svc.client = AsyncMock()
    return svc


@pytest.fixture
def es_service_no_client():
    """ElasticsearchService with client=None (not connected)."""
    return ElasticsearchService()


# =============================================================================
# Connection Tests
# =============================================================================


class TestConnection:
    @pytest.mark.asyncio
    async def test_connect_creates_client_and_indices(self, es_service):
        with patch("src.services.elasticsearch_service.AsyncElasticsearch") as mock_es:
            mock_client = AsyncMock()
            mock_es.return_value = mock_client
            es_service.client = None

            await es_service.connect()

            assert es_service.client is mock_client

    @pytest.mark.asyncio
    async def test_close_calls_client_close(self, es_service):
        await es_service.close()
        es_service.client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_noop_when_no_client(self, es_service_no_client):
        await es_service_no_client.close()  # Should not raise


# =============================================================================
# _create_indices Tests
# =============================================================================


class TestCreateIndices:
    @pytest.mark.asyncio
    async def test_creates_indices_when_not_existing(self, es_service):
        es_service.client.indices.exists = AsyncMock(return_value=False)
        es_service.client.indices.create = AsyncMock()

        await es_service._create_indices()

        assert es_service.client.indices.create.await_count == 3

    @pytest.mark.asyncio
    async def test_skips_existing_indices(self, es_service):
        es_service.client.indices.exists = AsyncMock(return_value=True)
        es_service.client.indices.create = AsyncMock()

        await es_service._create_indices()

        es_service.client.indices.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_client(self, es_service_no_client):
        await es_service_no_client._create_indices()  # Should not raise

    @pytest.mark.asyncio
    async def test_handles_create_index_exception(self, es_service):
        es_service.client.indices.exists = AsyncMock(return_value=False)
        es_service.client.indices.create = AsyncMock(side_effect=Exception("ES down"))

        await es_service._create_indices()  # Should not raise


# =============================================================================
# index_price_update / index_deal_update Tests
# =============================================================================


class TestIndexOperations:
    @pytest.mark.asyncio
    async def test_index_price_update_success(self, es_service):
        es_service.client.index = AsyncMock()

        result = await es_service.index_price_update({"asin": "B123", "price": 49.99})

        assert result is True

    @pytest.mark.asyncio
    async def test_index_price_update_returns_false_no_client(self, es_service_no_client):
        result = await es_service_no_client.index_price_update({"asin": "B123"})
        assert result is False

    @pytest.mark.asyncio
    async def test_index_deal_update_success(self, es_service):
        es_service.client.index = AsyncMock()

        result = await es_service.index_deal_update({"asin": "B456", "discount": 30})

        assert result is True

    @pytest.mark.asyncio
    async def test_index_deal_update_returns_false_no_client(self, es_service_no_client):
        result = await es_service_no_client.index_deal_update({"asin": "B456"})
        assert result is False

    @pytest.mark.asyncio
    async def test_index_with_retry_retries_on_failure(self, es_service):
        es_service.client.index = AsyncMock(
            side_effect=[Exception("timeout"), Exception("timeout"), None]
        )

        with patch("src.services.elasticsearch_service.asyncio.sleep", new_callable=AsyncMock):
            result = await es_service._index_with_retry("test-index", {"data": 1}, max_retries=3)

        assert result is True
        assert es_service.client.index.await_count == 3

    @pytest.mark.asyncio
    async def test_index_with_retry_fails_after_max_retries(self, es_service):
        es_service.client.index = AsyncMock(side_effect=Exception("persistent failure"))

        with patch("src.services.elasticsearch_service.asyncio.sleep", new_callable=AsyncMock):
            result = await es_service._index_with_retry("test-index", {"data": 1}, max_retries=3)

        assert result is False


# =============================================================================
# search_prices Tests
# =============================================================================


class TestSearchPrices:
    @pytest.mark.asyncio
    async def test_search_with_asin_filter(self, es_service):
        es_service.client.search = AsyncMock(return_value={"hits": {"hits": []}})

        result = await es_service.search_prices(asin="B123")

        call_kwargs = es_service.client.search.call_args.kwargs
        assert call_kwargs["query"] == {"bool": {"must": [{"term": {"asin": "B123"}}]}}

    @pytest.mark.asyncio
    async def test_search_with_price_range(self, es_service):
        es_service.client.search = AsyncMock(return_value={"hits": {"hits": []}})

        await es_service.search_prices(min_price=10.0, max_price=100.0)

        call_kwargs = es_service.client.search.call_args.kwargs
        query = call_kwargs["query"]
        assert query["bool"]["must"][0]["range"]["current_price"]["gte"] == 10.0
        assert query["bool"]["must"][0]["range"]["current_price"]["lte"] == 100.0

    @pytest.mark.asyncio
    async def test_search_match_all_when_no_filters(self, es_service):
        es_service.client.search = AsyncMock(return_value={"hits": {"hits": []}})

        await es_service.search_prices()

        call_kwargs = es_service.client.search.call_args.kwargs
        assert call_kwargs["query"] == {"match_all": {}}

    @pytest.mark.asyncio
    async def test_search_returns_empty_on_exception(self, es_service):
        es_service.client.search = AsyncMock(side_effect=Exception("ES error"))

        result = await es_service.search_prices(asin="B123")

        assert result == {"hits": []}

    @pytest.mark.asyncio
    async def test_search_returns_empty_no_client(self, es_service_no_client):
        result = await es_service_no_client.search_prices()
        assert result == {"hits": []}


# =============================================================================
# get_price_statistics Tests
# =============================================================================


class TestPriceStatistics:
    @pytest.mark.asyncio
    async def test_returns_aggregations(self, es_service):
        es_service.client.search = AsyncMock(
            return_value={"aggregations": {"price_stats": {"min": 10, "max": 100}}}
        )

        result = await es_service.get_price_statistics("B123")

        assert result == {"price_stats": {"min": 10, "max": 100}}

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self, es_service):
        es_service.client.search = AsyncMock(side_effect=Exception("fail"))

        result = await es_service.get_price_statistics("B123")

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_no_client(self, es_service_no_client):
        result = await es_service_no_client.get_price_statistics("B123")
        assert result == {}


# =============================================================================
# get_deal_aggregations Tests
# =============================================================================


class TestDealAggregations:
    @pytest.mark.asyncio
    async def test_returns_aggregations(self, es_service):
        es_service.client.search = AsyncMock(
            return_value={"aggregations": {"avg_price": {"value": 59.99}}}
        )

        result = await es_service.get_deal_aggregations(min_discount=20)

        assert result == {"avg_price": {"value": 59.99}}

    @pytest.mark.asyncio
    async def test_includes_domain_filter(self, es_service):
        es_service.client.search = AsyncMock(
            return_value={"aggregations": {}}
        )

        await es_service.get_deal_aggregations(domain="DE")

        call_kwargs = es_service.client.search.call_args.kwargs
        must_clauses = call_kwargs["query"]["bool"]["must"]
        domains = [c for c in must_clauses if "term" in c and "domain" in c.get("term", {})]
        assert len(domains) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self, es_service):
        es_service.client.search = AsyncMock(side_effect=Exception("fail"))
        result = await es_service.get_deal_aggregations()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_no_client(self, es_service_no_client):
        result = await es_service_no_client.get_deal_aggregations()
        assert result == {}


# =============================================================================
# get_deal_price_stats Tests
# =============================================================================


class TestDealPriceStats:
    @pytest.mark.asyncio
    async def test_returns_formatted_stats(self, es_service):
        es_service.client.search = AsyncMock(
            return_value={
                "aggregations": {
                    "price_stats": {"min": 29.99, "max": 89.99, "avg": 59.99, "count": 5},
                    "latest_price": {
                        "hits": {"hits": [{"_source": {"current_price": 49.99, "timestamp": "2026-02-20"}}]}
                    },
                    "price_over_time": {"buckets": []},
                }
            }
        )

        result = await es_service.get_deal_price_stats("B123")

        assert result["min"] == 29.99
        assert result["max"] == 89.99
        assert result["avg"] == 59.99
        assert result["current"] == 49.99
        assert result["data_points"] == 5

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self, es_service):
        es_service.client.search = AsyncMock(side_effect=Exception("fail"))
        result = await es_service.get_deal_price_stats("B123")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_no_client(self, es_service_no_client):
        result = await es_service_no_client.get_deal_price_stats("B123")
        assert result == {}


# =============================================================================
# delete_old_data Tests
# =============================================================================


class TestDeleteOldData:
    @pytest.mark.asyncio
    async def test_deletes_old_documents(self, es_service):
        es_service.client.delete_by_query = AsyncMock(return_value={"deleted": 42})

        result = await es_service.delete_old_data(days=90)

        assert result == 42
        es_service.client.delete_by_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_zero_on_exception(self, es_service):
        es_service.client.delete_by_query = AsyncMock(side_effect=Exception("fail"))

        result = await es_service.delete_old_data()

        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_no_client(self, es_service_no_client):
        result = await es_service_no_client.delete_old_data()
        assert result == 0
