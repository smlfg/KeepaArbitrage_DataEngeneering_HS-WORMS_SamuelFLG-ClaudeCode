"""
Comprehensive tests for Keepa API service

Covers:
- TokenStatus dataclass
- AsyncTokenBucket rate limiting
- KeepaAPIClient operations
- DealFilters
- Error handling
"""

import pytest
import time
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from dataclasses import asdict

from src.services.keepa_api import (
    TokenStatus,
    AsyncTokenBucket,
    KeepaAPIClient,
    DealFilters,
    KeepaAPIError,
    InvalidAsin,
    NoDealAccessError,
    TokenLimitError,
    TokenInsufficientError,
    get_keepa_client,
)


# =============================================================================
# TokenStatus Tests
# =============================================================================


class TestTokenStatus:
    """Tests for TokenStatus dataclass"""

    def test_tokens_needed_returns_true_when_sufficient_tokens(self):
        """tokens_needed returns True when sufficient tokens available"""
        status = TokenStatus(tokens_available=10)
        assert status.tokens_needed(5) is True

    def test_tokens_needed_returns_true_when_exact_match(self):
        """tokens_needed returns True when tokens exactly match cost"""
        status = TokenStatus(tokens_available=5)
        assert status.tokens_needed(5) is True

    def test_tokens_needed_returns_false_when_insufficient(self):
        """tokens_needed returns False when insufficient tokens"""
        status = TokenStatus(tokens_available=3)
        assert status.tokens_needed(5) is False

    def test_tokens_needed_returns_false_when_zero_tokens(self):
        """tokens_needed returns False when zero tokens available"""
        status = TokenStatus(tokens_available=0)
        assert status.tokens_needed(1) is False

    def test_time_until_refill_returns_zero_after_full_time(self):
        """time_until_refill returns 0 after refill interval elapsed"""
        status = TokenStatus(last_refill=time.time() - 60, refill_interval=60)
        assert status.time_until_refill() == 0

    def test_time_until_refill_returns_positive_before_refill(self):
        """time_until_refill returns positive value before refill time"""
        status = TokenStatus(last_refill=time.time(), refill_interval=60)
        remaining = status.time_until_refill()
        assert 0 < remaining <= 60

    def test_default_values(self):
        """TokenStatus has correct default values"""
        status = TokenStatus()
        assert status.tokens_available == 20
        assert status.tokens_per_minute == 20
        assert status.refill_interval == 60
        assert isinstance(status.last_refill, float)


# =============================================================================
# AsyncTokenBucket Tests
# =============================================================================


class TestAsyncTokenBucket:
    """Tests for AsyncTokenBucket rate limiting"""

    def test_init_sets_correct_defaults(self):
        """AsyncTokenBucket init sets correct defaults"""
        bucket = AsyncTokenBucket()
        assert bucket.tokens_per_minute == 20
        assert bucket.refill_interval == 60
        assert bucket.tokens_available == 20

    def test_init_with_custom_values(self):
        """AsyncTokenBucket accepts custom initialization values"""
        bucket = AsyncTokenBucket(tokens_per_minute=50, refill_interval=30)
        assert bucket.tokens_per_minute == 50
        assert bucket.refill_interval == 30
        assert bucket.tokens_available == 50

    def test_consume_succeeds_when_tokens_available(self):
        """consume() returns True and decrements tokens when available"""
        bucket = AsyncTokenBucket(tokens_per_minute=10)
        bucket.tokens_available = 10

        result = bucket.consume(3)

        assert result is True
        assert bucket.tokens_available == 7

    def test_consume_succeeds_with_exact_tokens(self):
        """consume() succeeds when tokens exactly match cost"""
        bucket = AsyncTokenBucket()
        bucket.tokens_available = 5

        result = bucket.consume(5)

        assert result is True
        assert bucket.tokens_available == 0

    def test_consume_fails_when_insufficient_tokens(self):
        """consume() returns False when insufficient tokens"""
        bucket = AsyncTokenBucket()
        bucket.tokens_available = 3

        result = bucket.consume(5)

        assert result is False
        assert bucket.tokens_available == 3  # Unchanged

    def test_consume_fails_when_zero_tokens(self):
        """consume() returns False when zero tokens available"""
        bucket = AsyncTokenBucket()
        bucket.tokens_available = 0

        result = bucket.consume(1)

        assert result is False

    def test_refill_adds_tokens_after_interval(self):
        """refill() adds tokens after interval elapsed"""
        bucket = AsyncTokenBucket(tokens_per_minute=20, refill_interval=60)
        bucket.tokens_available = 5
        bucket.last_refill = time.time() - 60

        added = bucket.refill()

        assert added == 15
        assert bucket.tokens_available == 20

    def test_refill_no_tokens_before_interval(self):
        """refill() returns 0 when interval not elapsed"""
        bucket = AsyncTokenBucket()
        bucket.tokens_available = 5
        bucket.last_refill = time.time()

        added = bucket.refill()

        assert added == 0
        assert bucket.tokens_available == 5

    def test_consume_triggers_refill(self):
        """consume() triggers refill before checking tokens"""
        bucket = AsyncTokenBucket(tokens_per_minute=20, refill_interval=60)
        bucket.tokens_available = 0
        bucket.last_refill = time.time() - 60

        result = bucket.consume(5)

        assert result is True
        assert bucket.tokens_available == 15

    @pytest.mark.asyncio
    async def test_wait_for_tokens_completes_immediately_when_available(self):
        """wait_for_tokens completes immediately when tokens available"""
        bucket = AsyncTokenBucket()
        bucket.tokens_available = 10

        start_time = time.time()
        result = await bucket.wait_for_tokens(5)
        elapsed = time.time() - start_time

        assert result is True
        assert elapsed < 0.1
        assert bucket.tokens_available == 5

    @pytest.mark.asyncio
    async def test_wait_for_tokens_waits_and_completes(self):
        """wait_for_tokens waits and completes when tokens become available"""
        bucket = AsyncTokenBucket(tokens_per_minute=20, refill_interval=60)
        bucket.tokens_available = 0
        bucket.last_refill = time.time() - 60  # Trigger refill on next check

        result = await bucket.wait_for_tokens(5, max_wait=120)

        assert result is True
        assert bucket.tokens_available == 15

    @pytest.mark.asyncio
    async def test_wait_for_tokens_raises_timeout(self):
        """wait_for_tokens raises TokenInsufficientError on timeout"""
        bucket = AsyncTokenBucket()
        bucket.tokens_available = 0
        bucket.last_refill = time.time()  # Just refilled, won't refill soon

        with pytest.raises(TokenInsufficientError) as exc_info:
            await bucket.wait_for_tokens(100, max_wait=0.01, check_interval=0.001)

        assert "Timeout" in str(exc_info.value)

    def test_get_status_returns_token_status(self):
        """get_status() returns TokenStatus with current state"""
        bucket = AsyncTokenBucket(tokens_per_minute=30)
        bucket.tokens_available = 25

        status = bucket.get_status()

        assert isinstance(status, TokenStatus)
        assert status.tokens_available == 25
        assert status.tokens_per_minute == 30


# =============================================================================
# DealFilters Tests
# =============================================================================


class TestDealFilters:
    """Tests for DealFilters dataclass"""

    def test_default_values(self):
        """DealFilters has correct default values"""
        filters = DealFilters()
        assert filters.page == 0
        assert filters.domain_id == 3  # Germany
        assert filters.min_rating == 4
        assert filters.min_reviews == 10
        assert filters.exclude_warehouses is True
        assert filters.sort == "SCORE"
        assert filters.min_discount == 20
        assert filters.max_discount == 90
        assert filters.min_price_cents == 500
        assert filters.max_price_cents == 50000

    def test_custom_values(self):
        """DealFilters accepts custom values"""
        filters = DealFilters(
            page=2,
            domain_id=1,
            min_rating=3,
            min_reviews=50,
            min_discount=30,
            max_discount=80,
        )
        assert filters.page == 2
        assert filters.domain_id == 1
        assert filters.min_rating == 3
        assert filters.min_reviews == 50

    def test_optional_fields_none_by_default(self):
        """Optional DealFilters fields default to None"""
        filters = DealFilters()
        assert filters.include_categories is None
        assert filters.exclude_categories is None
        assert filters.price_types is None


# =============================================================================
# KeepaAPIClient Tests
# =============================================================================


class TestKeepaAPIClient:
    """Tests for KeepaAPIClient"""

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_init_uses_api_key_from_parameter(self, mock_keepa, mock_settings):
        """init uses API key from parameter"""
        mock_settings.return_value = MagicMock(keepa_api_key="env_key")
        mock_keepa.return_value = MagicMock(tokens_left=20)

        client = KeepaAPIClient(api_key="param_key")

        mock_keepa.assert_called_once_with("param_key")
        assert client._api_key == "param_key"

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_init_uses_api_key_from_settings(self, mock_keepa, mock_settings):
        """init uses API key from settings when not provided"""
        mock_settings.return_value = MagicMock(keepa_api_key="env_key")
        mock_keepa.return_value = MagicMock(tokens_left=20)

        client = KeepaAPIClient()

        mock_keepa.assert_called_once_with("env_key")

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_init_initializes_token_bucket(self, mock_keepa, mock_settings):
        """init initializes AsyncTokenBucket"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_keepa.return_value = MagicMock(tokens_left=20)

        client = KeepaAPIClient()

        assert isinstance(client._token_bucket, AsyncTokenBucket)
        assert client._is_initialized is True

    @patch("src.services.keepa_api.get_settings")
    def test_init_handles_initialization_error(self, mock_settings):
        """init handles Keepa initialization error gracefully"""
        mock_settings.return_value = MagicMock(keepa_api_key="bad_key")

        with patch("src.services.keepa_api.Keepa") as mock_keepa:
            mock_keepa.side_effect = Exception("Invalid API key")
            client = KeepaAPIClient()

        assert client._is_initialized is False
        assert client._api is None

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_ensure_initialized_raises_when_not_initialized(
        self, mock_keepa, mock_settings
    ):
        """_ensure_initialized raises KeepaAPIError when not initialized"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_keepa.side_effect = Exception("Failed")

        client = KeepaAPIClient()

        with pytest.raises(KeepaAPIError) as exc_info:
            client._ensure_initialized()

        assert "not initialized" in str(exc_info.value)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_query_product_raises_on_invalid_asin(self, mock_keepa, mock_settings):
        """query_product raises InvalidAsin for invalid ASIN format"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_keepa.return_value = MagicMock(tokens_left=20)

        client = KeepaAPIClient()

        with pytest.raises(InvalidAsin) as exc_info:
            asyncio.run(client.query_product("SHORT"))

        assert "Invalid ASIN" in str(exc_info.value)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_query_product_raises_on_too_long_asin(self, mock_keepa, mock_settings):
        """query_product raises InvalidAsin for ASIN longer than 10 chars"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_keepa.return_value = MagicMock(tokens_left=20)

        client = KeepaAPIClient()

        with pytest.raises(InvalidAsin):
            asyncio.run(client.query_product("B0123456789"))

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_query_product_returns_data(self, mock_keepa, mock_settings):
        """query_product returns formatted data for valid ASIN"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20

        # Build proper csv array: each index is [keepa_time, price_cents, ...]
        csv_data = [None] * 20
        csv_data[0] = [1234567890, 9999]    # Amazon price: 99.99 EUR
        csv_data[4] = [1234567890, 12999]   # List price: 129.99 EUR
        csv_data[11] = [1234567890, 8999]   # Buy box: 89.99 EUR

        mock_api.query.return_value = [
            {
                "title": "Test Product",
                "rating": 45,  # 4.5 stars * 10 → normalized to 4.5
                "offers": 5,
                "csv": csv_data,
                "categories": [123, 456],
            }
        ]
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        result = asyncio.run(client.query_product("B08N5WRWNW"))

        assert result["asin"] == "B08N5WRWNW"
        assert result["title"] == "Test Product"
        assert result["current_price"] == 99.99
        assert result["list_price"] == 129.99
        assert result["buy_box_price"] == 89.99
        assert result["rating"] == 4.5
        assert result["offers_count"] == 5

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_query_product_raises_when_no_products(self, mock_keepa, mock_settings):
        """query_product raises KeepaAPIError when keepa returns empty list"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.query.return_value = []
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()

        with pytest.raises(KeepaAPIError) as exc_info:
            asyncio.run(client.query_product("B08N5WRWNW"))

        assert "No product found" in str(exc_info.value)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_search_deals_returns_deals(self, mock_keepa, mock_settings):
        """search_deals returns list of deals"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.deals.return_value = {
            "dr": [
                {
                    "asin": "B08N5WRWNW",
                    "title": "Great Deal",
                    "current": [9999, 7999, 0, 0, 12999],  # idx 0, 1, 4
                    "deltaPercent": [[20]],
                }
            ],
            "categoryNames": ["Electronics"],
        }
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        filters = DealFilters()
        result = asyncio.run(client.search_deals(filters))

        assert "deals" in result
        assert "total" in result
        assert "page" in result
        assert result["total"] == 1
        assert len(result["deals"]) == 1
        assert result["deals"][0]["asin"] == "B08N5WRWNW"

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_search_deals_applies_filters(self, mock_keepa, mock_settings):
        """search_deals applies DealFilters correctly"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.deals.return_value = {"dr": [], "categoryNames": []}
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        filters = DealFilters(
            page=2,
            domain_id=1,
            include_categories=[123, 456],
            exclude_categories=[789],
            price_types=["new"],
            min_discount=30,
            max_discount=70,
        )
        asyncio.run(client.search_deals(filters))

        call_args = mock_api.deals.call_args
        params = call_args[0][0]
        assert params["page"] == 2
        assert params["domainId"] == 1
        assert params["includeCategories"] == [123, 456]
        assert params["excludeCategories"] == [789]
        assert params["priceTypes"] == ["new"]
        assert params["deltaPercentRange"] == [30, 70]

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_search_deals_returns_empty_list_when_no_deals(
        self, mock_keepa, mock_settings
    ):
        """search_deals returns empty list when keepa returns no deals"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.deals.return_value = {"dr": [], "categoryNames": []}
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        filters = DealFilters()
        result = asyncio.run(client.search_deals(filters))

        assert result["deals"] == []
        assert result["total"] == 0

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_search_deals_raises_no_deal_access(self, mock_keepa, mock_settings):
        """search_deals raises NoDealAccessError on 404"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.deals.side_effect = Exception("404 NOT FOUND")
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        filters = DealFilters()

        with pytest.raises(NoDealAccessError) as exc_info:
            asyncio.run(client.search_deals(filters))

        assert "Deal API not available" in str(exc_info.value)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_search_deals_raises_token_limit(self, mock_keepa, mock_settings):
        """search_deals raises TokenLimitError on REQUEST_REJECTED"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 0
        mock_api.deals.side_effect = Exception("REQUEST_REJECTED")
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        filters = DealFilters()

        with pytest.raises(TokenLimitError) as exc_info:
            asyncio.run(client.search_deals(filters))

        assert "No tokens available" in str(exc_info.value)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_search_deals_raises_rate_limit(self, mock_keepa, mock_settings):
        """search_deals raises TokenLimitError on rate limit error"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.deals.side_effect = Exception("RATE limit exceeded")
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        filters = DealFilters()

        with pytest.raises(TokenLimitError) as exc_info:
            asyncio.run(client.search_deals(filters))

        assert "Rate limit exceeded" in str(exc_info.value)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_get_token_status_returns_dict(self, mock_keepa, mock_settings):
        """get_token_status returns status dict"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_keepa.return_value = MagicMock(tokens_left=20)

        client = KeepaAPIClient()
        # Manually set token bucket tokens for predictable test
        client._token_bucket.tokens_available = 15
        result = client.get_token_status()

        assert "tokens_available" in result
        assert "tokens_per_minute" in result
        assert "time_until_refill" in result
        assert result["tokens_available"] == 15

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_check_rate_limit_returns_status(self, mock_keepa, mock_settings):
        """check_rate_limit returns rate limit status"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 18
        type(mock_api).time_to_refill = PropertyMock(return_value=45)
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        result = client.check_rate_limit()

        assert result["tokens_available"] == 18
        assert result["refill_in_seconds"] == 45

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_check_rate_limit_handles_error(self, mock_keepa, mock_settings):
        """check_rate_limit handles errors gracefully when tokens_left raises"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        type(mock_api).tokens_left = PropertyMock(side_effect=Exception("API error"))
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        result = client.check_rate_limit()

        # _get_tokens_left catches the exception internally and returns None,
        # so check_rate_limit returns tokens_available=0 without an error key
        assert result["tokens_available"] == 0

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_get_price_history_returns_data(self, mock_keepa, mock_settings):
        """get_price_history returns price history data"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        # csv format: [timestamps, ...] with prices at index 3
        current_time = time.time()
        mock_api.query.return_value = [
            {
                "csv": [
                    [
                        current_time - 86400,
                        current_time - 43200,
                        current_time,
                    ],  # timestamps
                    [],
                    [],
                    [9999, 8999, 7999],  # new prices at index 3
                ]
            }
        ]
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        result = asyncio.run(client.get_price_history("B08N5WRWNW", days=30))

        assert isinstance(result, list)
        assert len(result) == 3
        assert all("price" in item for item in result)
        assert all("timestamp" in item for item in result)

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_get_price_history_returns_empty_on_error(self, mock_keepa, mock_settings):
        """get_price_history returns empty list on error"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 20
        mock_api.query.side_effect = Exception("API error")
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        result = asyncio.run(client.get_price_history("B08N5WRWNW"))

        assert result == []

    @patch("src.services.keepa_api.get_settings")
    @patch("src.services.keepa_api.Keepa")
    def test_update_token_status(self, mock_keepa, mock_settings):
        """update_token_status updates token bucket from API"""
        mock_settings.return_value = MagicMock(keepa_api_key="test_key")
        mock_api = MagicMock()
        mock_api.tokens_left = 25
        # status is a dict property (not a callable) set after each API call
        mock_api.status = {"tokensPerMin": 30, "refillIn": 45}
        mock_keepa.return_value = mock_api

        client = KeepaAPIClient()
        asyncio.run(client.update_token_status())

        assert client._token_bucket.tokens_per_minute == 30
        assert client._token_bucket.refill_interval == 45
        assert client._token_bucket.tokens_available == 25


# =============================================================================
# Singleton Functions Tests
# =============================================================================


class TestSingletonFunctions:
    """Tests for singleton getter functions"""

    @patch("src.services.keepa_api.KeepaAPIClient")
    def test_get_keepa_client_creates_singleton(self, mock_client_class):
        """get_keepa_client creates singleton instance"""
        mock_instance = MagicMock()
        mock_client_class.return_value = mock_instance

        # Clear singleton
        import src.services.keepa_api as keepa_module

        keepa_module._keepa_client = None

        client1 = get_keepa_client()
        client2 = get_keepa_client()

        assert client1 is client2
        mock_client_class.assert_called_once()



# =============================================================================
# Exception Classes Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exception classes"""

    def test_keepa_api_error_is_exception(self):
        """KeepaAPIError is a proper Exception"""
        with pytest.raises(KeepaAPIError):
            raise KeepaAPIError("test error")

    def test_invalid_asin_is_keepa_api_error(self):
        """InvalidAsin inherits from KeepaAPIError"""
        assert issubclass(InvalidAsin, KeepaAPIError)
        with pytest.raises(InvalidAsin):
            raise InvalidAsin("bad asin")

    def test_no_deal_access_error_is_keepa_api_error(self):
        """NoDealAccessError inherits from KeepaAPIError"""
        assert issubclass(NoDealAccessError, KeepaAPIError)
        with pytest.raises(NoDealAccessError):
            raise NoDealAccessError("no access")

    def test_token_limit_error_is_keepa_api_error(self):
        """TokenLimitError inherits from KeepaAPIError"""
        assert issubclass(TokenLimitError, KeepaAPIError)
        with pytest.raises(TokenLimitError):
            raise TokenLimitError("rate limited")

    def test_token_insufficient_error_is_token_limit_error(self):
        """TokenInsufficientError inherits from TokenLimitError"""
        assert issubclass(TokenInsufficientError, TokenLimitError)
        with pytest.raises(TokenInsufficientError):
            raise TokenInsufficientError("insufficient")
