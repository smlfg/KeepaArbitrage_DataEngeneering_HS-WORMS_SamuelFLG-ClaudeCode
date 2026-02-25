"""
Keepa API Client
Handles all interactions with Keepa API
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from src.utils.pipeline_logger import log_api_call, log_parser

    PIPELINE_LOGGING_AVAILABLE = True
except ImportError:
    PIPELINE_LOGGING_AVAILABLE = False

    def log_api_call(*args, **kwargs):
        pass

    def log_parser(*args, **kwargs):
        pass

    class PipelineStage:
        API_REQUEST = "api_request"
        PARSING = "parsing"
        FILTERING = "filtering"


import httpx

try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
    )
except ImportError:
    # Fallback: no-op decorator when tenacity is not installed
    def retry(**kwargs):
        def decorator(func):
            return func
        return decorator

    def stop_after_attempt(n):
        return n

    def wait_exponential(**kwargs):
        return None

    def retry_if_exception_type(types):
        return types

from src.config import get_keepa_api_key


KEEPA_API_BASE = "https://api.keepa.com"

logger = logging.getLogger("keepa_client")


class KeepaApiError(Exception):
    """Base exception for Keepa API errors"""

    pass


class KeepaRateLimitError(KeepaApiError):
    """Rate limit exceeded"""

    pass


class KeepaAuthError(KeepaApiError):
    """Authentication failed"""

    pass


class KeepaTimeoutError(KeepaApiError):
    """Request timed out"""

    pass


class NoDealAccessError(KeepaApiError):
    """Deals endpoint not available on this API plan (404)"""

    pass


class KeepaClient:
    """
    Client for Keepa API
    Handles authentication, rate limiting, and response parsing
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_keepa_api_key()
        self.api_key_hash = self._hash_api_key(self.api_key)
        self.rate_limit_remaining: int = 100
        self.rate_limit_reset: Optional[int] = None
        self._es_service = None

    def _hash_api_key(self, key: str) -> str:
        """Hash API key for logging (security)"""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _get_es_service(self):
        """Lazy-load ES service to avoid circular imports."""
        if self._es_service is None:
            try:
                from src.services.elasticsearch_service import es_service
                self._es_service = es_service
            except Exception:
                pass
        return self._es_service

    async def _log_token_metric(self, response: dict, endpoint: str,
                                 response_time_ms: int, domain: str = "",
                                 asin_count: int = 0, success: bool = True,
                                 error: str = ""):
        """Write token metric to ES after each API call."""
        es = self._get_es_service()
        if not es:
            return
        metric = {
            "timestamp": datetime.utcnow().isoformat(),
            "operation": endpoint,
            "tokens_consumed": response.get("tokensConsumed", 0),
            "tokens_left": response.get("tokensLeft", self.rate_limit_remaining),
            "refill_rate": response.get("refillRate", 0),
            "refill_in": response.get("refillIn", 0),
            "response_time_ms": response_time_ms,
            "asin_count": asin_count,
            "domain": domain,
            "success": success,
            "error": error,
        }
        try:
            await es.index_token_metric(metric)
        except Exception:
            pass

    async def _make_request(
        self, endpoint: str, params: dict, timeout: float = 30.0, method: str = "GET"
    ) -> dict:
        """
        Make API request with token-aware rate limiting.
        Waits for token refill when budget is low before sending request.
        Uses query parameter authentication (key=...)
        """
        url = f"{KEEPA_API_BASE}/{endpoint}"

        # Token-aware pre-check: wait if tokens are low
        if self.rate_limit_remaining < 10 and self.rate_limit_reset:
            wait_ms = max(self.rate_limit_reset, 1000)
            wait_s = min(wait_ms / 1000, 60)  # Cap at 60s
            logger.info(
                f"Token budget low ({self.rate_limit_remaining}), "
                f"waiting {wait_s:.0f}s for refill..."
            )
            await asyncio.sleep(wait_s)

        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "POST":
                response = await client.post(url, data=params)
            else:
                response = await client.get(url, params=params)

            # Update rate limit info from response headers
            self.rate_limit_remaining = int(
                response.headers.get("X-RateLimit-Remaining", 100)
            )
            self.rate_limit_reset = int(response.headers.get("X-RateLimit-Reset", 0))

            if response.status_code == 200:
                return response.json()

            elif response.status_code == 401:
                raise KeepaAuthError("Invalid Keepa API key")

            elif response.status_code == 429:
                # Read reset time from response and update state
                reset_ms = int(response.headers.get("X-RateLimit-Reset", 15000))
                self.rate_limit_remaining = 0
                self.rate_limit_reset = reset_ms
                raise KeepaRateLimitError(
                    f"Rate limit exceeded. Reset in {reset_ms}ms. "
                    f"Remaining: {self.rate_limit_remaining}"
                )

            elif response.status_code == 404:
                raise NoDealAccessError(
                    f"Keepa endpoint not available on this plan (404)"
                )

            elif response.status_code == 504:
                raise KeepaTimeoutError("Keepa API timeout")

            else:
                error_msg = response.text[:200] if response.text else "Unknown error"
                raise KeepaApiError(
                    f"Keepa API error {response.status_code}: {error_msg}"
                )

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=15, max=90),
        retry=retry_if_exception_type((KeepaRateLimitError, KeepaTimeoutError)),
    )
    async def search_products(
        self,
        search_term: str,
        domain_id: int = 3,
        category: Optional[int] = None,
        page: int = 1,
    ) -> dict:
        """
        Search for products using Keepa API

        Args:
            search_term: Keyword or phrase to search
            domain_id: Amazon domain (1=com, 3=de, 4=co.uk)
            category: Category ID to filter by
            page: Page number (1-based)

        Returns:
            Parsed search response with products
        """
        params = {
            "key": self.api_key,
            "domain": domain_id,
            "type": "product",
            "term": search_term,
            "page": page,
        }

        if category:
            params["category"] = category

        start_time = time.time()

        try:
            response = await self._make_request("search", params)

            execution_time = int((time.time() - start_time) * 1000)

            await self._log_token_metric(response, "search", execution_time)

            return {
                "raw": response,
                "metadata": {
                    "request_id": str(uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "execution_time_ms": execution_time,
                    "tokens_consumed": response.get("tokensConsumed", 0),
                    "products_found": len(response.get("products", [])),
                    "search_term": search_term,
                },
            }

        except (KeepaRateLimitError, KeepaTimeoutError) as e:
            raise

        except KeepaApiError as e:
            raise KeepaApiError(f"Search failed: {str(e)}")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=15, max=90),
        retry=retry_if_exception_type((KeepaRateLimitError, KeepaTimeoutError)),
    )
    async def get_products(
        self,
        asins: list[str],
        domain_id: int = 3,  # Amazon.de
    ) -> dict:
        """
        Get product details from Keepa API

        Args:
            asins: List of ASINs to fetch
            domain_id: Amazon domain (1=com, 3=de, 4=co.uk)

        Returns:
            Parsed product response
        """
        params = {"key": self.api_key, "domain": domain_id, "asin": ",".join(asins)}
        domain_names = {1: "US", 2: "UK", 3: "DE", 4: "FR", 8: "IT", 9: "ES", 14: "NL"}

        start_time = time.time()

        try:
            response = await self._make_request("product", params)

            execution_time = int((time.time() - start_time) * 1000)

            await self._log_token_metric(
                response, "query", execution_time,
                domain=domain_names.get(domain_id, str(domain_id)),
                asin_count=len(asins),
            )

            return {
                "raw": response,
                "metadata": {
                    "request_id": str(uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "execution_time_ms": execution_time,
                    "tokens_consumed": response.get("tokensConsumed", 0),
                    "products_found": len(response.get("products", [])),
                },
            }

        except (KeepaRateLimitError, KeepaTimeoutError) as e:
            raise

        except KeepaApiError as e:
            raise KeepaApiError(f"Product fetch failed: {str(e)}")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=15, max=90),
        retry=retry_if_exception_type((KeepaRateLimitError, KeepaTimeoutError)),
    )
    async def get_bestsellers(
        self,
        domain_id: int,
        category: int,
    ) -> dict:
        """
        Get bestseller ASINs for a category.

        Args:
            domain_id: Amazon domain (1=com, 2=uk, 3=de, 4=fr, 8=it, 9=es)
            category: Category browse node ID

        Returns:
            Dict with raw response and metadata including asin_count
        """
        params = {
            "key": self.api_key,
            "domain": domain_id,
            "category": category,
        }

        start_time = time.time()

        domain_names = {1: "US", 2: "UK", 3: "DE", 4: "FR", 8: "IT", 9: "ES", 14: "NL"}

        try:
            response = await self._make_request("bestsellers", params)
            execution_time = int((time.time() - start_time) * 1000)

            asin_list = response.get("bestSellersList") or []

            await self._log_token_metric(
                response, "bestsellers", execution_time,
                domain=domain_names.get(domain_id, str(domain_id)),
                asin_count=len(asin_list),
            )

            return {
                "raw": response,
                "metadata": {
                    "request_id": str(uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "execution_time_ms": execution_time,
                    "tokens_consumed": response.get("tokensConsumed", 0),
                    "asin_count": len(asin_list),
                },
            }

        except (KeepaRateLimitError, KeepaTimeoutError):
            raise
        except KeepaApiError as e:
            raise KeepaApiError(f"Bestsellers fetch failed: {str(e)}")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=15, max=90),
        retry=retry_if_exception_type((KeepaRateLimitError, KeepaTimeoutError)),
    )
    async def search_categories(
        self,
        term: str,
        domain_id: int,
    ) -> dict:
        """
        Search for category browse node IDs by name.

        Args:
            term: Search term (e.g. "keyboard")
            domain_id: Amazon domain

        Returns:
            Raw response with category matches
        """
        params = {
            "key": self.api_key,
            "domain": domain_id,
            "term": term,
            "type": "category",
        }

        response = await self._make_request("search", params)
        return response

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=15, max=90),
        retry=retry_if_exception_type((KeepaRateLimitError, KeepaTimeoutError)),
    )
    async def product_finder(
        self,
        domain_id: int,
        product_parms: dict,
    ) -> dict:
        """
        Find products matching filter criteria via Keepa Product Finder.

        Args:
            domain_id: Amazon domain
            product_parms: Filter criteria dict (productType, rootCategory, salesRankRange, etc.)

        Returns:
            Dict with raw response and metadata including asin_count
        """
        import json as _json

        params = {
            "key": self.api_key,
            "domain": domain_id,
            "selection": _json.dumps(product_parms),
        }

        start_time = time.time()

        domain_names = {1: "US", 2: "UK", 3: "DE", 4: "FR", 8: "IT", 9: "ES", 14: "NL"}

        try:
            response = await self._make_request("query", params, method="POST")
            execution_time = int((time.time() - start_time) * 1000)

            asin_list = response.get("asinList") or []

            await self._log_token_metric(
                response, "product_finder", execution_time,
                domain=domain_names.get(domain_id, str(domain_id)),
                asin_count=len(asin_list),
            )

            return {
                "raw": response,
                "metadata": {
                    "request_id": str(uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "execution_time_ms": execution_time,
                    "tokens_consumed": response.get("tokensConsumed", 0),
                    "asin_count": len(asin_list),
                },
            }

        except (KeepaRateLimitError, KeepaTimeoutError):
            raise
        except KeepaApiError as e:
            raise KeepaApiError(f"Product finder failed: {str(e)}")

    async def get_deals(
        self,
        domain_id: int = 3,
        min_discount: int = 10,
        limit: int = 50,
        price_types: list = None,
        include_categories: list = None,
    ) -> list[dict]:
        """
        Fetch current deals from Keepa Deals API.

        priceTypes: 0=Amazon, 1=New 3rd party, 2=Used/WHD, 10=Collectible, 11=Refurbished
        Amazon WHD = priceType 2

        Args:
            domain_id: Amazon domain (1=com, 3=de, 4=fr, 8=it, 9=es, 14=nl)
            min_discount: Minimum discount percentage
            limit: Max number of deals to return
            price_types: Keepa price types to include
            include_categories: Keepa category IDs to filter by

        Returns:
            List of deal dicts with asin, title, price, discount etc.
        """
        domain_names = {3: "DE", 4: "FR", 8: "IT", 9: "ES", 14: "NL", 2: "UK", 1: "US"}

        params = {"key": self.api_key, "domain": domain_id, "page": 0}

        if include_categories:
            params["includeCategories"] = ",".join(str(c) for c in include_categories)
        if price_types:
            params["priceTypes"] = ",".join(str(p) for p in price_types)

        start_time = time.time()
        try:
            response = await self._make_request("deals", params)
            execution_time = int((time.time() - start_time) * 1000)
            domain_name = domain_names.get(domain_id, str(domain_id))
            tokens = response.get("tokensConsumed", 0)
            logger.debug(f"Keepa API tokens consumed for {domain_name}: {tokens}")
            await self._log_token_metric(
                response, "deals", execution_time, domain=domain_name,
            )
            items = (
                response if isinstance(response, list) else response.get("deals", [])
            )
            deals = []
            for item in items:
                deals.append(
                    {
                        "asin": item.get("asin"),
                        "title": item.get("title"),
                        "current_price": item.get("current", 0) / 100
                        if item.get("current")
                        else None,
                        "list_price": item.get("avg90", 0) / 100
                        if item.get("avg90")
                        else None,
                        "discount_percent": item.get("deltaPercent"),
                        "rating": item.get("rating", 0) / 10
                        if item.get("rating")
                        else None,
                        "reviews": item.get("reviews"),
                        "domain": domain_name,
                    }
                )
            return deals

        except NoDealAccessError:
            raise
        except KeepaApiError as e:
            raise KeepaApiError(f"Deals fetch failed: {str(e)}")

    @staticmethod
    def _get_latest_price(csv_array) -> Optional[float]:
        """Extract the latest price from a Keepa CSV price history array.

        Keepa CSV format: [timestamp1, price1, timestamp2, price2, ...]
        Latest price = last element. Prices are integers (divide by 100 for EUR).
        -1 means price unavailable.
        """
        if csv_array is None:
            return None
        if not csv_array or len(csv_array) < 2:
            return None
        price_int = csv_array[-1]
        if price_int == -1:
            if PIPELINE_LOGGING_AVAILABLE:
                logger.debug("Price data unavailable (-1) for ASIN")
            return None
        return price_int / 100.0

    async def get_products_with_deals(
        self,
        asins: list[str],
        domain_id: int = 3,
        min_discount: int = 10,
    ) -> list[dict]:
        """
        Fetch products from /product endpoint and find WHD/Used deals via price heuristic.

        CSV indices:
          csv[0] = Amazon price history
          csv[2] = Used/Marketplace price history
          csv[9] = Warehouse Deal (WHD) price history

        Args:
            asins: List of ASINs to check (max 100 per call recommended)
            domain_id: Amazon domain (3=DE, 4=FR, 8=IT, 9=ES, 14=NL)
            min_discount: Minimum discount % to qualify as a deal

        Returns:
            List of deals with snake_case fields and source="product_heuristic"
        """
        domain_names = {3: "DE", 4: "FR", 8: "IT", 9: "ES", 14: "NL", 2: "UK", 1: "US"}
        domain_name = domain_names.get(domain_id, str(domain_id))

        params = {
            "key": self.api_key,
            "domain": domain_id,
            "asin": ",".join(asins),
        }

        # Logging disabled - fix parameter mismatch
        # if PIPELINE_LOGGING_AVAILABLE:
        #     log_api_call(
        #         asins=asins, domain=domain_name, tokens_consumed=0, response_time_ms=0
        #     )

        try:
            start_time = time.time()
            response = await self._make_request("product", params)
            response_time_ms = int((time.time() - start_time) * 1000)
            tokens = response.get("tokensConsumed", 0)

            logger.debug(
                f"Keepa /product {domain_name}: {len(asins)} ASINs, {tokens} tokens"
            )
            await self._log_token_metric(
                response, "query", response_time_ms,
                domain=domain_name, asin_count=len(asins),
            )

            products = response.get("products", [])
            deals = []
            prices_null_count = 0

            for product in products:
                try:
                    asin = product.get("asin")
                    title = product.get("title") or f"Product {asin}"
                    csv_data = product.get("csv") or []

                    amazon_price = self._get_latest_price(
                        csv_data[0]
                        if len(csv_data) > 0 and csv_data[0] is not None
                        else None
                    )
                    new_price = self._get_latest_price(
                        csv_data[1]
                        if len(csv_data) > 1 and csv_data[1] is not None
                        else None
                    )
                    used_price = self._get_latest_price(
                        csv_data[2]
                        if len(csv_data) > 2 and csv_data[2] is not None
                        else None
                    )
                    whd_price = self._get_latest_price(
                        csv_data[9]
                        if len(csv_data) > 9 and csv_data[9] is not None
                        else None
                    )

                    prices_null = (
                        amazon_price is None
                        and new_price is None
                        and used_price is None
                        and whd_price is None
                    )

                    # Logging disabled - PipelineStage not defined
                    # if PIPELINE_LOGGING_AVAILABLE:
                    #     log_parser(
                    #         asin=asin,
                    #         domain=domain_name,
                    #         extracted_fields={
                    #             "amazon_price": amazon_price,
                    #             "new_price": new_price,
                    #             "used_price": used_price,
                    #             "whd_price": whd_price,
                    #             "list_price": amazon_price
                    #             or new_price
                    #             or used_price
                    #             or whd_price,
                    #             "deal_price": whd_price or used_price or new_price,
                    #             "deal_type": "WHD"
                    #             if whd_price
                    #             else (
                    #                 "Used"
                    #                 if used_price
                    #                 else ("New" if new_price else None)
                    #             ),
                    #         },
                    #     )

                    # Log which prices are missing for debugging
                    if prices_null:
                        prices_null_count += 1
                        logger.debug(f"ASIN {asin}: No price data available from Keepa")

                    # Use new_price as primary fallback, then used_price, then whd_price
                    list_price = amazon_price or new_price or used_price or whd_price

                    # Pick best deal price (WHD preferred over Used, then Used)
                    deal_price = None
                    deal_type = None
                    if whd_price is not None:
                        deal_price = whd_price
                        deal_type = "WHD"
                    elif used_price is not None:
                        deal_price = used_price
                        deal_type = "Used"
                    elif new_price is not None:
                        deal_price = new_price
                        deal_type = "New"

                    if deal_price is None or list_price is None or list_price <= 0:
                        continue

                    if deal_price <= 0:
                        logger.debug(
                            f"ASIN {asin}: Skipping - deal_price is 0 or negative: {deal_price}"
                        )
                        continue

                    discount_pct = int((1 - deal_price / list_price) * 100)
                    if discount_pct < min_discount:
                        continue

                    rating_raw = product.get("rating", 0)
                    deals.append(
                        {
                            "asin": asin,
                            "title": title,
                            "current_price": deal_price,
                            "list_price": list_price,
                            "discount_percent": discount_pct,
                            "rating": rating_raw / 10.0 if rating_raw else None,
                            "reviews": product.get("reviewCount"),
                            "domain": domain_name,
                            "deal_type": deal_type,
                            "source": "product_heuristic",
                        }
                    )

                except Exception as e:
                    logger.warning(f"Skipping ASIN {product.get('asin')}: {e}")
                    continue

            # Logging disabled - fix parameter mismatch
            # if PIPELINE_LOGGING_AVAILABLE:
            #     log_api_call(
            #         asins=asins,
            #         domain=domain_name,
            #         tokens_consumed=tokens,
            #         response_time_ms=response_time_ms,
            #         deals_found=len(deals),
            #         prices_null=prices_null_count,
            #     )

            return deals

        except NoDealAccessError:
            raise
        except KeepaApiError as e:
            raise KeepaApiError(f"Product deal fetch failed: {str(e)}")

    def parse_products(self, raw_response: dict) -> list[dict]:
        """
        Parse raw Keepa product response into structured format

        Args:
            raw_response: Raw API response

        Returns:
            List of parsed product dictionaries
        """
        products = []
        raw_products = raw_response.get("products", [])

        for product in raw_products:
            parsed = {
                "asin": product.get("asin"),
                "title": product.get("title")
                or f"Product {product.get('asin', 'Unknown')}",
                "category": self._extract_category(product),
                "current_price": self._parse_price_from_csv(
                    product.get("csv"), price_type="current"
                ),
                "original_price": self._parse_price_from_csv(
                    product.get("csv"), price_type="avg"
                ),
                "discount_percent": self._calculate_discount(product),
                "rating": self._parse_rating(product),
                "review_count": self._get_review_count(product),
                "sales_rank": self._extract_sales_rank(product),
                "amazon_url": self._build_amazon_url(
                    product.get("asin"), domain_id=raw_response.get("domain", 3)
                ),
                "image_url": self._extract_image(product),
                "is_amazon_seller": self._check_amazon_seller(product),
                "last_updated": datetime.utcnow().isoformat(),
                "raw_data": product,  # Keep raw data for reference
            }
            products.append(parsed)

        return products

    def _extract_category(self, product: dict) -> Optional[str]:
        """Extract category from product"""
        category_tree = product.get("categoryTree", [])
        if category_tree:
            last_cat = category_tree[-1]
            if isinstance(last_cat, dict):
                return last_cat.get("name")
            return str(last_cat)
        return product.get("productGroup") or product.get("binding")

    def _parse_price_from_csv(
        self, csv_data, price_type: str = "current"
    ) -> Optional[Decimal]:
        """
        Parse price from Keepa CSV data

        CSV format: [amazon_prices, new_prices, used_prices, ...]
        Each price array contains [price, timestamp, price, timestamp, ...] pairs

        Args:
            csv_data: The CSV array from Keepa response
            price_type: "current", "avg", or "new"

        Returns:
            Price as Decimal or None
        """
        if not csv_data or not isinstance(csv_data, list):
            return None

        # Index mapping for price types
        price_indices = {
            "current": 0,  # Amazon price
            "new": 1,  # New price
            "used": 2,  # Used price
        }

        idx = price_indices.get(price_type, 0)
        if idx >= len(csv_data):
            return None

        price_array = csv_data[idx]
        if not price_array or not isinstance(price_array, list):
            return None

        # Find latest non-null price (iterate backwards through pairs)
        # Format: [price1, timestamp1, price2, timestamp2, ...]
        for i in range(len(price_array) - 2, -1, -2):
            price_val = price_array[i]
            if price_val is not None and price_val > 0:
                return Decimal(price_val) / 100  # Convert from cents

        return None

    def _calculate_discount(self, product: dict) -> Optional[int]:
        """Calculate discount percentage from CSV data"""
        csv_data = product.get("csv")
        if not csv_data or not isinstance(csv_data, list) or len(csv_data) < 2:
            return None

        # Get current Amazon price and average new price
        current = self._parse_price_from_csv(csv_data, "current")
        new_price = self._parse_price_from_csv(csv_data, "new")

        if current and new_price and float(new_price) > 0:
            discount = int((1 - float(current) / float(new_price)) * 100)
            return max(0, discount)

        return None

    def _parse_rating(self, product: dict) -> Optional[Decimal]:
        """Parse rating from product"""
        rating = product.get("rating")
        if rating and rating > 0:
            return Decimal(rating) / 20  # Keepa stores rating * 20
        return None

    def _get_review_count(self, product: dict) -> Optional[int]:
        """Get review count from product"""
        # Keepa stores review count in a specific format
        reviews = product.get("reviews")
        if isinstance(reviews, dict):
            return reviews.get("count") or reviews.get("ratedCount")
        elif isinstance(reviews, int) and reviews > 0:
            return reviews
        return None

    def _extract_sales_rank(self, product: dict) -> Optional[int]:
        """Extract sales rank from product"""
        # Sales rank might be in different fields
        sales_rank = product.get("salesRankReference")
        if sales_rank and sales_rank > 0:
            return sales_rank

        sales_ranks = product.get("salesRanks")
        if sales_ranks and isinstance(sales_ranks, list) and len(sales_ranks) > 0:
            # Format: [rank, categoryId, timestamp]
            if isinstance(sales_ranks[0], list) and len(sales_ranks[0]) > 0:
                return sales_ranks[0][0]
            elif isinstance(sales_ranks[0], int):
                return sales_ranks[0]

        return None

    def _extract_image(self, product: dict) -> Optional[str]:
        """Extract image URL from product"""
        images_csv = product.get("imagesCSV")
        if images_csv:
            first_image = images_csv.split(",")[0]
            if first_image:
                return f"https://images-eu.ssl-images-amazon.com/images/I/{first_image}"
        return None

    def _check_amazon_seller(self, product: dict) -> bool:
        """Check if Amazon is the seller (buyBox)"""
        buy_box = product.get("buyBoxSellerIdHistory", [])
        if buy_box and len(buy_box) > 0:
            # Amazon's seller ID is ATVPDKIKX0DER
            return buy_box[-1] == "ATVPDKIKX0DER"
        return False

    def _build_amazon_url(self, asin: str, domain_id: int = 3) -> str:
        """Build Amazon product URL"""
        if not asin:
            return ""

        domain_map = {
            1: "amazon.com",
            3: "amazon.de",
            4: "amazon.co.uk",
            5: "amazon.fr",
        }
        domain = domain_map.get(domain_id, "amazon.de")
        return f"https://www.{domain}/dp/{asin}"


# Singleton instance
_keepa_client: Optional[KeepaClient] = None


def get_keepa_client() -> KeepaClient:
    """Get or create Keepa client singleton"""
    global _keepa_client
    if _keepa_client is None:
        _keepa_client = KeepaClient()
    return _keepa_client


async def close_keepa_client():
    """Cleanup Keepa client"""
    global _keepa_client
    _keepa_client = None
