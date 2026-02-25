"""
Keepa API Client for Keeper System
Uses official keepa library (keepa==1.5.x)
With async Token Bucket management for rate limiting
"""

import asyncio
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

try:
    from src.utils.pipeline_logger import log_api_call, log_parser
    _PIPELINE_LOG = True
except ImportError:
    _PIPELINE_LOG = False
from concurrent.futures import ThreadPoolExecutor

try:
    from keepa import Keepa
except ImportError:  # pragma: no cover - fallback for offline/test envs

    class Keepa:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "keepa package not installed. Please install keepa>=1.5.0."
            )


from src.config import get_settings

logger = logging.getLogger(__name__)


class KeepaAPIError(Exception):
    """Base exception for Keepa API errors"""

    pass


class InvalidAsin(KeepaAPIError):
    """Raised when ASIN is invalid"""

    pass


class NoDealAccessError(KeepaAPIError):
    """Raised when user doesn't have deal API access"""

    pass


class TokenLimitError(KeepaAPIError):
    """Raised when rate limit is exceeded"""

    pass


class TokenInsufficientError(TokenLimitError):
    """Raised when not enough tokens available"""

    pass


@dataclass
class DealFilters:
    """Filters for deal search"""

    page: int = 0
    domain_id: int = 3  # 3 = Germany (DE)
    include_categories: Optional[List[int]] = None
    exclude_categories: Optional[List[int]] = None
    price_types: Optional[List[str]] = None
    min_rating: int = 4
    min_reviews: int = 10
    exclude_warehouses: bool = True
    sort: str = "SCORE"
    min_discount: int = 20
    max_discount: int = 90
    min_price_cents: int = 500
    max_price_cents: int = 50000


@dataclass
class TokenStatus:
    """Token bucket status"""

    tokens_available: int = 20
    tokens_per_minute: int = 20
    last_refill: float = field(default_factory=time.time)
    refill_interval: int = 60  # seconds

    def tokens_needed(self, cost: int) -> bool:
        return self.tokens_available >= cost

    def time_until_refill(self) -> float:
        elapsed = time.time() - self.last_refill
        remaining = self.refill_interval - elapsed
        return max(0, remaining)


class AsyncTokenBucket:
    """
    Async Token Bucket for rate limiting Keepa API calls.

    Implements a token bucket algorithm where:
    - Tokens refill at a constant rate (20 per 60 seconds by default)
    - Each API call consumes a specific number of tokens
    - If not enough tokens, automatically waits for refill
    """

    def __init__(
        self,
        tokens_per_minute: int = 20,
        refill_interval: int = 60,
        executor: Optional[ThreadPoolExecutor] = None,
    ):
        self.tokens_per_minute = tokens_per_minute
        self.refill_interval = refill_interval
        self.tokens_available = tokens_per_minute
        self.last_refill = time.time()
        self.executor = executor or ThreadPoolExecutor(max_workers=4)
        self._lock = asyncio.Lock()

    def refill(self) -> int:
        """
        Refill tokens based on time elapsed.

        Returns:
            Number of tokens refilled
        """
        now = time.time()
        elapsed = now - self.last_refill

        if elapsed >= self.refill_interval:
            # Full refill
            tokens_added = self.tokens_per_minute - self.tokens_available
            self.tokens_available = self.tokens_per_minute
            self.last_refill = now
            if tokens_added > 0:
                logger.info(f"🔄 Token bucket refilled: +{tokens_added} tokens")
            return tokens_added

        return 0

    def consume(self, cost: int) -> bool:
        """
        Try to consume tokens from the bucket.

        Args:
            cost: Number of tokens to consume

        Returns:
            True if successful, False if insufficient tokens
        """
        self.refill()

        if self.tokens_available >= cost:
            self.tokens_available -= cost
            logger.info(
                f"📊 Token consumed: -{cost}, Remaining: {self.tokens_available}"
            )
            return True

        return False

    async def wait_for_tokens(
        self, cost: int, max_wait: float = 120.0, check_interval: float = 5.0
    ) -> bool:
        """
        Wait until enough tokens are available (thread-safe via asyncio.Lock).

        Args:
            cost: Number of tokens needed
            max_wait: Maximum time to wait in seconds
            check_interval: How often to check token status

        Returns:
            True if tokens obtained, False if timeout
        """
        async with self._lock:
            start_time = time.time()

            while True:
                self.refill()

                if self.tokens_available >= cost:
                    self.tokens_available -= cost
                    wait_time = time.time() - start_time
                    if wait_time > 1:
                        logger.info(f"⏳ Waited {wait_time:.1f}s for tokens")
                    logger.info(
                        f"📊 Token consumed: -{cost}, Remaining: {self.tokens_available}"
                    )
                    return True

                elapsed = time.time() - start_time
                if elapsed >= max_wait:
                    logger.warning(f"⏰ Timeout waiting for tokens after {max_wait}s")
                    raise TokenInsufficientError(
                        f"Timeout after {max_wait}s waiting for {cost} tokens. "
                        f"Currently have {self.tokens_available} tokens."
                    )

                time_until_refill = self.refill_interval - (time.time() - self.last_refill)
                wait_for = min(check_interval, time_until_refill)

                logger.info(f"⏳ Waiting {wait_for:.1f}s for token refill...")
                await asyncio.sleep(wait_for)

    def get_status(self) -> TokenStatus:
        """Get current token bucket status"""
        self.refill()
        return TokenStatus(
            tokens_available=self.tokens_available,
            tokens_per_minute=self.tokens_per_minute,
            last_refill=self.last_refill,
            refill_interval=self.refill_interval,
        )


class KeepaAPIClient:
    """
    Keepa API Client for Amazon price monitoring and deal finding.

    Features:
    - Async token bucket for rate limiting
    - Automatic token refill from Keepa API status
    - Thread-safe operations
    - Proper error handling

    Domain IDs: 1=US, 2=GB, 3=DE, 4=FR, 5=JP, 6=CA, 7=CN, 8=IT, 9=ES, 10=IN, 11=MX, 12=BR
    """

    DOMAIN_MAP = {
        1: "US",
        2: "GB",
        3: "DE",
        4: "FR",
        5: "JP",
        6: "CA",
        7: "CN",
        8: "IT",
        9: "ES",
        10: "IN",
        11: "MX",
        12: "BR",
    }

    # Token costs for different API calls (approximate)
    TOKEN_COSTS = {
        "query": 15,  # Product query
        "deals": 5,  # Deal search
        "category": 5,  # Category lookup
        "best_sellers": 3,  # Best sellers
        "seller": 5,  # Seller query
    }

    _es_service_ref = None

    @classmethod
    def _get_es_service(cls):
        if cls._es_service_ref is None:
            try:
                from src.services.elasticsearch_service import es_service
                cls._es_service_ref = es_service
            except Exception:
                pass
        return cls._es_service_ref

    async def _log_token_metric(self, operation: str, tokens_consumed: int,
                                 response_time_ms: float, domain: str = "",
                                 asin_count: int = 0, success: bool = True,
                                 error: str = ""):
        es = self._get_es_service()
        if not es:
            return
        tokens_left = self._get_tokens_left()
        metric = {
            "timestamp": datetime.utcnow().isoformat(),
            "operation": operation,
            "tokens_consumed": tokens_consumed,
            "tokens_left": tokens_left if tokens_left is not None else 0,
            "refill_rate": self._token_bucket.tokens_per_minute,
            "refill_in": int(self._token_bucket.refill_interval - (time.time() - self._token_bucket.last_refill)),
            "response_time_ms": int(response_time_ms),
            "asin_count": asin_count,
            "domain": domain,
            "success": success,
            "error": error,
        }
        try:
            await es.index_token_metric(metric)
        except Exception:
            pass

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Keepa API client with async token management.

        Args:
            api_key: Keepa API key (defaults to settings.KEEPA_API_KEY)
        """
        settings = get_settings()
        self._api_key = api_key or settings.keepa_api_key
        self._init_error: Optional[str] = None

        # Initialize Keepa API
        if not self._api_key:
            self._api = None
            self._is_initialized = False
            self._init_error = (
                "Missing KEEPA_API_KEY. Set KEEPA_API_KEY in your .env file."
            )
            logger.error(f"❌ Failed to initialize Keepa API: {self._init_error}")
        else:
            try:
                self._api = Keepa(self._api_key)
                self._is_initialized = True
                logger.info("✅ Keepa API initialized successfully")
            except Exception as e:
                self._init_error = str(e)
                logger.error(f"❌ Failed to initialize Keepa API: {e}")
                self._api = None
                self._is_initialized = False

        # Initialize token bucket — sync with real Keepa token count if available
        initial_tokens = 200  # safe default; Keepa plans typically have 100-5000/min
        if self._api is not None:
            real_tokens = self._get_tokens_left()
            if real_tokens is not None and real_tokens > 0:
                initial_tokens = real_tokens
                logger.info(f"🔄 Token bucket initialized from Keepa API: {initial_tokens} tokens")
        self._token_bucket = AsyncTokenBucket(tokens_per_minute=initial_tokens, refill_interval=60)

        # Thread pool for sync Keepa calls
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Cumulative token counter for session-level monitoring
        self.total_tokens_consumed = 0

    def _ensure_initialized(self):
        """Ensure API is initialized before use"""
        if not self._is_initialized or self._api is None:
            detail = self._init_error or "Check API key and keepa package installation."
            raise KeepaAPIError(f"Keepa API not initialized. {detail}")

    def _get_api_attr(self, *names: str, default=None):
        """Read the first available attribute from the Keepa client object."""
        if self._api is None:
            return default
        for name in names:
            if hasattr(self._api, name):
                return getattr(self._api, name)
        return default

    def _get_tokens_left(self) -> Optional[int]:
        """Read token count across Keepa naming variants."""
        if self._api is None:
            return None

        for name in ("tokens_left", "tokensLeft"):
            try:
                value = getattr(self._api, name)
            except Exception:
                continue

            if value is None:
                continue

            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue

            if isinstance(value, (int, float, str)):
                try:
                    return int(value)
                except Exception:
                    continue

        return None

    async def _sync_call(self, func, *args, **kwargs):
        """Run a synchronous Keepa API call in executor"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))

    async def _api_call_with_retry(self, func, max_retries: int = 3, timeout: float = 60.0):
        """Execute an API call with retry logic and timeout.

        Retries on server errors (500/502/503) and connection errors with
        exponential backoff (2s, 4s, 8s).  Non-retryable errors are raised
        immediately.

        Args:
            func: Callable that performs the synchronous API call
            max_retries: Maximum number of attempts
            timeout: Per-attempt timeout in seconds
        """
        last_exception: Exception | None = None

        for attempt in range(max_retries):
            try:
                return await asyncio.wait_for(
                    self._sync_call(func),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                last_exception = KeepaAPIError(
                    f"API call timed out after {timeout}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                logger.warning(
                    f"⏰ API timeout (attempt {attempt + 1}/{max_retries})"
                )
            except Exception as e:
                error_msg = str(e).upper()
                # Retry on server errors (500, 502, 503)
                retryable_codes = ("500", "502", "503", "SERVER ERROR",
                                   "BAD GATEWAY", "SERVICE UNAVAILABLE")
                # Retry on connection / network errors
                retryable_net = ("CONNECTION", "CONNECT", "DNS", "NETWORK",
                                 "TIMEOUT", "TIMED OUT")

                if any(code in error_msg for code in retryable_codes):
                    last_exception = e
                    logger.warning(
                        f"🔄 Server error (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                elif any(term in error_msg for term in retryable_net):
                    last_exception = e
                    logger.warning(
                        f"🔄 Connection error (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                else:
                    # Non-retryable error — raise immediately
                    raise

            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                logger.info(f"⏳ Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

        raise KeepaAPIError(
            f"API call failed after {max_retries} retries: {last_exception}"
        )

    async def update_token_status(self):
        """Update token bucket from Keepa API status.

        The keepa library sets ``self._api.status`` as a ``Status`` dataclass
        (with attributes ``tokensLeft``, ``refillIn``, ``refillRate``),
        **not** a plain dict.  We support both formats so that unit-tests
        using dict mocks keep working.
        """
        try:
            status = self._api.status if self._api else None
            if not status:
                return

            # Handle both dict (legacy/mocks) and Status dataclass (real keepa lib)
            if isinstance(status, dict):
                tokens_per_min = status.get("tokensPerMin", 20) or 20
                refill_in = status.get("refillIn", 60) or 60
            elif hasattr(status, "refillRate"):
                # Status dataclass: tokensLeft, refillIn, refillRate, timestamp
                tokens_per_min = status.refillRate or 20
                refill_in = status.refillIn or 60
                # Keepa API returns refillIn in milliseconds — convert to seconds
                if refill_in > 1000:
                    refill_in = refill_in / 1000.0
            else:
                return

            self._token_bucket.tokens_per_minute = int(tokens_per_min)
            self._token_bucket.refill_interval = int(refill_in)

            # Sync available tokens from real Keepa state
            tokens_left = self._get_tokens_left()
            if tokens_left is not None:
                self._token_bucket.tokens_available = tokens_left

            logger.debug(
                f"🔄 Token status updated: {tokens_left}/{tokens_per_min} tokens"
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not update token status: {e}")

    async def query_product(self, asin: str, domain_id: int = 3) -> Dict[str, Any]:
        """
        Query single product by ASIN with token management.

        Args:
            asin: Amazon product ASIN (10 characters)
            domain_id: Keepa domain id (3=DE, 2=GB, 4=FR, 8=IT, 9=ES)

        Returns:
            dict with product data:
            {
                'asin': str,
                'title': str,
                'current_price': float,
                'list_price': float,
                'category': str,
                'rating': float,
                'offers_count': int,
                'buy_box_price': float,
                'price_history_count': int,
                'timestamp': int
            }

        Raises:
            InvalidAsin: If ASIN is not 10 characters
            KeepaAPIError: If API returns an error
            TokenLimitError: If rate limit exceeded
        """
        self._ensure_initialized()

        if len(asin) != 10:
            raise InvalidAsin(f"Invalid ASIN: {asin}. Must be 10 characters.")

        domain = self.DOMAIN_MAP.get(domain_id)
        if not domain:
            raise KeepaAPIError(f"Unsupported domain_id: {domain_id}")

        # Wait for tokens
        cost = self.TOKEN_COSTS["query"]
        await self._token_bucket.wait_for_tokens(cost)
        self.total_tokens_consumed += cost
        logger.info(f"Total tokens consumed this session: {self.total_tokens_consumed}")

        try:
            # Make API call (with retry + 60s timeout)
            _t0 = time.time()
            products = await self._api_call_with_retry(
                lambda: self._api.query(asin, domain=domain, stats=90, history=True, offers=20)
            )
            _elapsed_ms = (time.time() - _t0) * 1000

            # Pipeline logging: API call
            if _PIPELINE_LOG:
                log_api_call(asins=[asin], domain=domain, tokens_consumed=cost, response_time_ms=_elapsed_ms)

            # Sync token bucket with real Keepa API status
            await self.update_token_status()

            # Log token metric to ES
            await self._log_token_metric(
                "query", cost, _elapsed_ms,
                domain=domain, asin_count=1,
            )

            if not products:
                raise KeepaAPIError(f"No product found for ASIN: {asin}")

            product = products[0]

            # Extract prices from keepa library csv arrays
            # csv indices: 0=Amazon, 1=New, 2=Used, 3=Sales Rank, 4=List Price,
            #   5=Collectible, 6=Refurbished, 7=New FBA, 8=Lightning Deals,
            #   9=Warehouse Deals, 10=New FBM Shipping, 11=Buy Box,
            #   12=Used Like New, 13=Used Very Good, 14=Used Good,
            #   15=Used Acceptable, 16=Rating, 17=Review Count,
            #   18=Buy Box Used, 19=Sales Rank Drops (30d)
            # Each csv[i] is [keepa_time, value, keepa_time, value, ...] in cents
            # Values: -1 = not available, -2 = no data
            csv_data = product.get("csv", [])

            def _last_valid_price(arr):
                """Get last valid price from a keepa csv array (cents -> EUR)."""
                if arr is None or not hasattr(arr, '__len__') or len(arr) < 2:
                    return 0
                # Walk backwards through price values (odd indices)
                for i in range(len(arr) - 1, 0, -2):
                    val = arr[i]
                    if isinstance(val, (int, float)) and val > 0:
                        return val / 100.0
                return 0

            current_price = 0
            list_price = 0
            buy_box = 0

            if csv_data and isinstance(csv_data, (list, tuple)):
                # Priority: Amazon(0) > Buy Box(11) > New FBA(7) > New 3rd(1)
                #           > Used Like New(12) > Buy Box Used(18) > Warehouse(9)
                for idx in [0, 11, 7, 1, 12, 18, 9]:
                    if len(csv_data) > idx and csv_data[idx] is not None:
                        p = _last_valid_price(csv_data[idx])
                        if p > 0:
                            current_price = p
                            break

                # List price from index 4
                if len(csv_data) > 4 and csv_data[4] is not None:
                    list_price = _last_valid_price(csv_data[4])

                # Buy box from index 11, fallback to Buy Box Used(18)
                for bb_idx in [11, 18]:
                    if len(csv_data) > bb_idx and csv_data[bb_idx] is not None:
                        bb = _last_valid_price(csv_data[bb_idx])
                        if bb > 0:
                            buy_box = bb
                            break

            # Stats-fallback if csv parsing yielded 0 prices
            stats_data = product.get("stats")
            if stats_data and isinstance(stats_data, dict):
                current_arr = stats_data.get("current", [])
                if isinstance(current_arr, (list, tuple)):
                    if current_price == 0:
                        # Search ALL price indices in stats.current
                        for idx in [0, 11, 7, 1, 12, 18, 9, 5, 6, 8, 13, 14, 15]:
                            if len(current_arr) > idx and current_arr[idx] is not None:
                                val = current_arr[idx]
                                if isinstance(val, (int, float)) and val > 0:
                                    current_price = val / 100.0
                                    break

                    if buy_box == 0:
                        for idx in [11, 18]:
                            if len(current_arr) > idx and current_arr[idx] is not None:
                                val = current_arr[idx]
                                if isinstance(val, (int, float)) and val > 0:
                                    buy_box = val / 100.0
                                    break

                    if list_price == 0 and len(current_arr) > 4:
                        val = current_arr[4]
                        if isinstance(val, (int, float)) and val > 0:
                            list_price = val / 100.0

                # Named stat fields as additional fallback
                if buy_box == 0:
                    buy_box_stat = stats_data.get("buyBoxPrice")
                    if isinstance(buy_box_stat, (int, float)) and buy_box_stat > 0:
                        buy_box = buy_box_stat / 100.0
                if list_price == 0:
                    list_stat = stats_data.get("listPrice")
                    if isinstance(list_stat, (int, float)) and list_stat > 0:
                        list_price = list_stat / 100.0

            # Offers-based fallback: extract price from offers array
            if current_price == 0:
                offers_data = product.get("offers")
                if offers_data and isinstance(offers_data, list):
                    for offer in offers_data:
                        if not isinstance(offer, dict):
                            continue
                        offer_price = offer.get("offerCSV")
                        if offer_price is not None:
                            p = _last_valid_price(offer_price)
                            if p > 0:
                                current_price = p
                                break

            # If still no current_price, try buyBoxPrice from product root
            if current_price == 0:
                root_bb = product.get("buyBoxPrice")
                if isinstance(root_bb, (int, float)) and root_bb > 0:
                    current_price = root_bb / 100.0

            # Rating: prefer product.rating, fallback to stats.current[16] / 10.0
            rating = product.get("rating", 0) or 0
            if isinstance(rating, (int, float)) and rating > 10:
                rating = rating / 10.0
            if (not rating or rating <= 0) and stats_data and isinstance(stats_data, dict):
                current_arr = stats_data.get("current", [])
                if isinstance(current_arr, (list, tuple)) and len(current_arr) > 16:
                    r = current_arr[16]
                    if isinstance(r, (int, float)) and r > 0:
                        rating = r / 10.0

            offers = product.get("offers", 0) or 0
            if isinstance(offers, list):
                offers = len(offers)

            history_count = (
                len([c for c in csv_data if c is not None]) if csv_data else 0
            )

            category = ""
            if product.get("categories") and product["categories"]:
                category = str(product["categories"][-1])

            logger.info(
                f"📊 {asin}: price={current_price}€, list={list_price}€, "
                f"buybox={buy_box}€, rating={rating}"
            )

            result = {
                "asin": asin,
                "title": product.get("title", "Unknown"),
                "current_price": current_price,
                "list_price": list_price,
                "category": category,
                "rating": float(rating),
                "offers_count": int(offers),
                "buy_box_price": buy_box,
                "price_history_count": history_count,
                "timestamp": int(datetime.utcnow().timestamp()),
            }

            # Pipeline logging: parser results
            if _PIPELINE_LOG:
                missing = [k for k, v in result.items() if k in ("current_price", "title", "rating") and not v]
                log_parser(asin=asin, extracted_fields=result, missing_fields=missing)

            return result

        except KeepaAPIError:
            raise
        except Exception as e:
            raise KeepaAPIError(f"Error querying product {asin}: {str(e)}")

    async def search_deals(self, filters: DealFilters) -> Dict[str, Any]:
        """
        Search for deals using Keepa deals() with token management.

        Args:
            filters: DealFilters object with search parameters

        Returns:
            dict with deals data:
            {
                'deals': [...],
                'total': int,
                'page': int,
                'category_names': list[str]
            }

        Raises:
            NoDealAccessError: If deal API not available for account
            TokenLimitError: If rate limit exceeded
        """
        self._ensure_initialized()

        # Wait for tokens
        cost = self.TOKEN_COSTS["deals"]
        await self._token_bucket.wait_for_tokens(cost)
        self.total_tokens_consumed += cost
        logger.info(f"Total tokens consumed this session: {self.total_tokens_consumed}")

        try:
            # Build deal search parameters
            # Valid keys: page, domainId, excludeCategories, includeCategories,
            # priceTypes, deltaRange, deltaPercentRange, deltaLastRange,
            # salesRankRange, currentRange, minRating, isLowest, isLowestOffer,
            # isOutOfStock, titleSearch, isRangeEnabled, isFilterEnabled,
            # hasReviews, filterErotic, sortType, dateRange
            deal_params = {
                "page": filters.page,
                "domainId": filters.domain_id,
                "hasReviews": filters.min_reviews > 0,
                "isFilterEnabled": True,
                "isRangeEnabled": True,
                "deltaPercentRange": [filters.min_discount, filters.max_discount],
                "currentRange": [filters.min_price_cents, filters.max_price_cents],
            }

            # Note: minRating crashes keepa lib — filter locally instead

            if filters.include_categories:
                deal_params["includeCategories"] = filters.include_categories

            if filters.exclude_categories:
                deal_params["excludeCategories"] = filters.exclude_categories

            if filters.price_types:
                deal_params["priceTypes"] = filters.price_types

            # Call deals API (with retry + 60s timeout)
            _t0 = time.time()
            result = await self._api_call_with_retry(
                lambda: self._api.deals(
                    deal_params, domain=self.DOMAIN_MAP.get(filters.domain_id, "DE")
                )
            )
            _elapsed_ms = (time.time() - _t0) * 1000
            await self._log_token_metric(
                "deals", cost, _elapsed_ms,
                domain=self.DOMAIN_MAP.get(filters.domain_id, "DE"),
            )

            # Parse deals — Keepa returns deals in 'dr' key
            # 'current' array indices: 0=Amazon, 1=New3rdParty, 4=ListPrice, 7=New FBA
            # 'deltaPercent' array indices match 'current' — [0] = Amazon price change %
            # Prices are in cents, -1 means N/A
            deals = []
            deal_list = result.get("dr", []) if result else []
            for deal in deal_list:
                cur = deal.get("current", [])

                # Best available price: Amazon (0) > New FBA (7) > New 3rd party (1)
                price = 0
                for idx in [0, 7, 1]:
                    if isinstance(cur, list) and len(cur) > idx and cur[idx] > 0:
                        price = cur[idx] / 100.0
                        break

                # List price at index 4
                list_price = 0
                if isinstance(cur, list) and len(cur) > 4 and cur[4] > 0:
                    list_price = cur[4] / 100.0

                if list_price <= 0:
                    list_price = price

                # Discount from deltaPercent (first relevant index)
                delta_pct = deal.get("deltaPercent", [])
                discount = 0
                if isinstance(delta_pct, list) and len(delta_pct) > 0:
                    for row in delta_pct:
                        if isinstance(row, list) and len(row) > 0 and row[0] != 0:
                            discount = abs(row[0])
                            break
                        elif isinstance(row, (int, float)) and row > 0:
                            discount = abs(row)
                            break

                # Fallback: calculate from prices
                if (
                    discount == 0
                    and list_price > 0
                    and price > 0
                    and list_price > price
                ):
                    discount = round((1 - price / list_price) * 100, 1)

                # Rating from current array index 16
                rating = 0
                if isinstance(cur, list) and len(cur) > 16 and cur[16] > 0:
                    rating = cur[16] / 10.0

                # Review count from current array index 17
                reviews = 0
                if isinstance(cur, list) and len(cur) > 17 and cur[17] > 0:
                    reviews = cur[17]

                if price <= 0:
                    continue

                deals.append(
                    {
                        "asin": deal.get("asin", ""),
                        "title": deal.get("title", "Unknown"),
                        "current_price": price,
                        "list_price": list_price,
                        "discount_percent": discount,
                        "rating": rating,
                        "prime_eligible": False,
                        "reviews": reviews,
                        "url": f"https://amazon.de/dp/{deal.get('asin', '')}",
                    }
                )

            return {
                "deals": deals,
                "total": len(deal_list),
                "page": filters.page,
                "category_names": result.get("categoryNames", []) if result else [],
            }

        except Exception as e:
            error_msg = str(e).upper()
            tokens_left = self._get_tokens_left()

            if "REQUEST_REJECTED" in error_msg or tokens_left == 0:
                raise TokenLimitError(
                    "No tokens available. Please wait for token refill."
                )
            if "404" in error_msg or "NOT FOUND" in error_msg:
                raise NoDealAccessError("Deal API not available for this account.")
            if "RATE" in error_msg:
                raise TokenLimitError(
                    "Rate limit exceeded. Please wait before trying again."
                )
            raise KeepaAPIError(f"Error searching deals: {str(e)}")

    async def get_price_history(
        self, asin: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get price history for product over last N days."""
        self._ensure_initialized()

        # Wait for tokens
        cost = self.TOKEN_COSTS["query"]
        await self._token_bucket.wait_for_tokens(cost)
        self.total_tokens_consumed += cost
        logger.info(f"Total tokens consumed this session: {self.total_tokens_consumed}")

        try:
            _t0 = time.time()
            products = await self._api_call_with_retry(
                lambda: self._api.query(asin, domain=self.DOMAIN_MAP[3])
            )
            _elapsed_ms = (time.time() - _t0) * 1000
            await self._log_token_metric("query", cost, _elapsed_ms, domain="DE", asin_count=1)

            if not products:
                return []

            product = products[0]
            history = []
            csv_data = product.get("csv", [])

            if csv_data and len(csv_data) > 1:
                timestamps = csv_data[0]
                new_prices = csv_data[3] if len(csv_data) > 3 else []
                cutoff = datetime.utcnow().timestamp() - (days * 24 * 60 * 60)

                for i, ts in enumerate(timestamps):
                    if ts >= cutoff:
                        price = new_prices[i] / 100.0 if i < len(new_prices) else 0
                        if price > 0:
                            history.append(
                                {
                                    "timestamp": int(ts),
                                    "price": price,
                                    "currency": "EUR",
                                }
                            )

            return sorted(history, key=lambda x: x["timestamp"])

        except Exception as e:
            logger.error(f"Error getting price history for {asin}: {e}")
            return []

    def get_token_status(self) -> Dict[str, Any]:
        """Get current token bucket status."""
        status = self._token_bucket.get_status()
        return {
            "tokens_available": status.tokens_available,
            "tokens_per_minute": status.tokens_per_minute,
            "last_refill": datetime.fromtimestamp(status.last_refill).isoformat(),
            "refill_interval": status.refill_interval,
            "time_until_refill": status.time_until_refill(),
            "initialized": self._is_initialized,
            "init_error": self._init_error,
            "total_tokens_consumed": self.total_tokens_consumed,
        }

    def check_rate_limit(self) -> Dict[str, Any]:
        """Check current rate limit status from Keepa API."""
        self._ensure_initialized()

        try:
            tokens_left = self._get_tokens_left()
            refill_time = self._get_api_attr(
                "time_to_refill",
                "timeToRefill",
                "refillIn",
                default=0,
            )

            if callable(refill_time):
                try:
                    refill_time = refill_time()
                except:
                    refill_time = 0

            try:
                refill_time = int(refill_time or 0)
            except Exception:
                refill_time = 0

            return {
                "tokens_available": tokens_left if tokens_left is not None else 0,
                "tokens_per_minute": 20,
                "refill_in_seconds": refill_time,
                "refill_in_minutes": refill_time // 60 if refill_time else 0,
            }
        except Exception as e:
            return {
                "tokens_available": 0,
                "tokens_per_minute": 20,
                "error": str(e),
            }


# Singleton instance
_keepa_client: Optional[KeepaAPIClient] = None


def get_keepa_client() -> KeepaAPIClient:
    """Get or create the Keepa API client singleton"""
    global _keepa_client
    if _keepa_client is None:
        _keepa_client = KeepaAPIClient()
    return _keepa_client
