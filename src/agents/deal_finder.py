import logging
import csv
from pathlib import Path
from typing import List, Dict, Any

from src.services.keepa_api import get_keepa_client
from src.services.notification import notification_service
from src.services.elasticsearch_service import es_service
from src.config import get_settings

logger = logging.getLogger(__name__)


class DealFinderAgent:
    MIN_RATING = 3.5
    MIN_DEALS_FOR_REPORT = 5
    MAX_DEALS_PER_REPORT = 15
    DROPSHIPPER_KEYWORDS = ["dropship", "fast shipping", "free shipping"]
    MIN_PRICE_THRESHOLD = 10.0

    # QWERTZ Keyboard seed ASINs for Amazon.de deal discovery.
    # Loaded from data/seed_asins_eu_qwertz.txt at runtime when available.
    DEFAULT_SEED_ASINS = [
        "B005EOWBHC",  # Logitech K120 QWERTZ
        "B00F34GN18",  # Cherry Stream Keyboard
        "B0058UR5GS",  # Cherry KC 1000
        "B07W6JN8V8",  # Logitech K380
        "B07VBFK1C4",  # Logitech MX Keys
        "B09DFY1LKY",  # Logitech MX Mechanical
        "B09FXYV8P9",  # Corsair K70 RGB
        "B0B6BCXRDS",  # Razer BlackWidow V4
        "B09V3KXJPB",  # Logitech MX Keys Mini
        "B07W7Q58J7",  # Logitech K270 Wireless
    ]

    DOMAIN_CODE_MAP = {
        1: "us",
        2: "uk",
        3: "de",
        4: "fr",
        8: "it",
        9: "es",
    }
    DOMAIN_HOST_MAP = {
        1: "amazon.com",
        2: "amazon.co.uk",
        3: "amazon.de",
        4: "amazon.fr",
        8: "amazon.it",
        9: "amazon.es",
    }

    def __init__(self):
        self.settings = get_settings()

    def _parse_seed_asins(self, raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            values = [str(x).strip().upper() for x in raw]
        else:
            values = [x.strip().upper() for x in str(raw).split(",")]
        return [v for v in values if len(v) == 10]

    def _normalize_domain_id(self, value: Any, default: int = 3) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return parsed if parsed in self.DOMAIN_CODE_MAP else default

    def _domain_code(self, domain_id: int) -> str:
        return self.DOMAIN_CODE_MAP.get(domain_id, "de")

    def _domain_host(self, domain_id: int) -> str:
        return self.DOMAIN_HOST_MAP.get(domain_id, "amazon.de")

    def _build_target(
        self,
        asin: str,
        domain_id: Any = 3,
        market: Any = None,
        source: str = "seed",
    ) -> Dict[str, Any]:
        normalized_asin = str(asin).strip().upper()
        if len(normalized_asin) != 10:
            return {}

        normalized_domain_id = self._normalize_domain_id(domain_id)
        market_name = str(market).strip().upper() if market is not None else ""
        if not market_name:
            market_name = self._domain_code(normalized_domain_id).upper()

        return {
            "asin": normalized_asin,
            "domain_id": normalized_domain_id,
            "domain": self._domain_code(normalized_domain_id),
            "market": market_name,
            "source": source,
        }

    def _load_target_file_targets(self) -> List[Dict[str, Any]]:
        raw_path = str(self.settings.deal_targets_file or "").strip()
        if not raw_path:
            return []

        path = Path(raw_path)
        if not path.is_absolute():
            repo_root = Path(__file__).resolve().parents[2]
            path = repo_root / path

        if not path.exists():
            return []

        targets: List[Dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    target = self._build_target(
                        asin=row.get("asin", ""),
                        domain_id=row.get("domain_id", 3),
                        market=row.get("market"),
                        source=str(row.get("source", "targets_file") or "targets_file"),
                    )
                    if target:
                        targets.append(target)
        except Exception as e:
            logger.warning(f"Could not read target file {path}: {e}")
            return []

        if targets:
            logger.info("Loaded %s seed targets from %s", len(targets), path)
        return targets

    def _parse_seed_targets(self, raw: Any, default_domain_id: int = 3) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []

        if raw is None:
            return targets

        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    target = self._build_target(
                        asin=item.get("asin", ""),
                        domain_id=item.get("domain_id", default_domain_id),
                        market=item.get("market"),
                        source=str(item.get("source", "config") or "config"),
                    )
                    if target:
                        targets.append(target)
                    continue

                if isinstance(item, str):
                    asin = item.strip().upper()
                    domain_id = default_domain_id
                    if ":" in asin:
                        parts = asin.split(":")
                        asin = parts[0].strip().upper()
                        if len(parts) > 1:
                            domain_id = self._normalize_domain_id(parts[1], default_domain_id)
                    target = self._build_target(
                        asin=asin,
                        domain_id=domain_id,
                        source="config",
                    )
                    if target:
                        targets.append(target)

            return targets

        for asin in self._parse_seed_asins(raw):
            target = self._build_target(
                asin=asin,
                domain_id=default_domain_id,
                source="config",
            )
            if target:
                targets.append(target)

        return targets

    def _dedupe_targets(self, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        deduped = []
        for target in targets:
            asin = target.get("asin")
            domain_id = self._normalize_domain_id(target.get("domain_id", 3))
            key = (asin, domain_id)
            if not asin or key in seen:
                continue
            seen.add(key)
            normalized = dict(target)
            normalized["domain_id"] = domain_id
            normalized["domain"] = self._domain_code(domain_id)
            normalized["market"] = str(
                normalized.get("market", self._domain_code(domain_id).upper())
            ).upper()
            deduped.append(normalized)
        return deduped

    def _get_seed_targets(self, filter_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        default_domain_id = self._normalize_domain_id(filter_config.get("domain_id", 3))

        configured_targets = self._parse_seed_targets(
            filter_config.get("seed_targets"),
            default_domain_id=default_domain_id,
        )
        if configured_targets:
            return self._dedupe_targets(configured_targets)

        file_targets = self._load_target_file_targets()
        if file_targets:
            return self._dedupe_targets(file_targets)

        seed_asins = self._get_seed_asins(filter_config)
        if seed_asins:
            return self._dedupe_targets(
                [
                    self._build_target(asin=asin, domain_id=default_domain_id, source="seed_asins")
                    for asin in seed_asins
                ]
            )

        return []

    def _get_seed_asins(self, filter_config: Dict[str, Any]) -> List[str]:
        provided = self._parse_seed_asins(filter_config.get("seed_asins"))
        if provided:
            return provided

        env_asins = self._parse_seed_asins(self.settings.deal_seed_asins)
        if env_asins:
            return env_asins

        seed_file_asins = self._load_seed_file_asins()
        if seed_file_asins:
            return seed_file_asins

        return self.DEFAULT_SEED_ASINS

    def _load_seed_file_asins(self) -> List[str]:
        raw_path = str(self.settings.deal_seed_file or "").strip()
        if not raw_path:
            return []

        path = Path(raw_path)
        if not path.is_absolute():
            repo_root = Path(__file__).resolve().parents[2]
            path = repo_root / path

        if not path.exists():
            return []

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not read seed file {path}: {e}")
            return []

        # Parse line-by-line, skip comments and blank lines
        asins = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Take first token (ASIN) in case of inline comments
            token = line.split()[0].upper()
            if len(token) == 10:
                asins.append(token)

        if asins:
            logger.info("Loaded %d seed ASINs from %s", len(asins), path)
        return asins

    def _select_candidate_asins(
        self, seed_asins: List[str], max_asins: int, start_offset: int
    ) -> List[str]:
        if not seed_asins:
            return []

        total = len(seed_asins)
        take = max(1, min(max_asins, total))
        offset = start_offset % total
        return [seed_asins[(offset + i) % total] for i in range(take)]

    def _select_candidate_targets(
        self, seed_targets: List[Dict[str, Any]], max_asins: int, start_offset: int
    ) -> List[Dict[str, Any]]:
        if not seed_targets:
            return []

        total = len(seed_targets)
        take = max(1, min(max_asins, total))
        offset = start_offset % total
        return [seed_targets[(offset + i) % total] for i in range(take)]

    def _normalize_deal(self, deal: Dict[str, Any]) -> Dict[str, Any]:
        asin = deal.get("asin", "")
        domain_id = self._normalize_domain_id(deal.get("domain_id", 3))
        domain = str(deal.get("domain", self._domain_code(domain_id))).lower()
        market = str(deal.get("market", domain.upper())).upper()
        host = self._domain_host(domain_id)
        current_price = float(deal.get("current_price", deal.get("currentPrice", 0)) or 0)
        list_price = float(
            deal.get(
                "list_price",
                deal.get("listPrice", deal.get("original_price", current_price)),
            )
            or current_price
        )
        if list_price <= 0:
            list_price = current_price

        discount_percent = deal.get("discount_percent", deal.get("discountPercent"))
        if discount_percent is None:
            if list_price > current_price > 0:
                discount_percent = round((1 - current_price / list_price) * 100, 1)
            else:
                discount_percent = 0.0

        return {
            "asin": asin,
            "title": deal.get("title", "Unknown"),
            "current_price": current_price,
            "list_price": list_price,
            "discount_percent": float(discount_percent or 0),
            "rating": float(deal.get("rating", 0) or 0),
            "reviews": int(
                deal.get("reviews", deal.get("review_count", deal.get("reviewCount", 0)))
                or 0
            ),
            "sales_rank": int(deal.get("sales_rank", deal.get("salesRank", 100000)) or 100000),
            "prime_eligible": bool(deal.get("prime_eligible", False)),
            "url": deal.get("url", deal.get("amazonUrl", f"https://{host}/dp/{asin}")),
            "source": deal.get("source", "product_api"),
            "category": deal.get("category"),
            "domain_id": domain_id,
            "domain": domain,
            "market": market,
        }

    def _build_deal_from_product(
        self,
        product: Dict[str, Any],
        domain_id: int = 3,
        market: str = "DE",
    ) -> Dict[str, Any]:
        asin = product.get("asin", "")
        normalized_domain_id = self._normalize_domain_id(domain_id)
        normalized_market = str(market or self._domain_code(normalized_domain_id)).upper()
        host = self._domain_host(normalized_domain_id)
        current_price = float(product.get("current_price", 0) or 0)
        list_price = float(product.get("list_price", current_price) or current_price)

        if list_price <= 0:
            list_price = current_price

        discount_percent = (
            round((1 - current_price / list_price) * 100, 1)
            if list_price > current_price > 0
            else 0.0
        )

        return {
            "asin": asin,
            "title": product.get("title", "Unknown"),
            "current_price": current_price,
            "list_price": list_price,
            "discount_percent": discount_percent,
            "rating": float(product.get("rating", 0) or 0),
            "reviews": int(product.get("offers_count", 0) or 0),
            "sales_rank": int(product.get("sales_rank", 100000) or 100000),
            "prime_eligible": bool(product.get("prime_eligible", False)),
            "url": f"https://{host}/dp/{asin}",
            "source": "product_api",
            "category": str(product.get("category", "") or ""),
            "domain_id": normalized_domain_id,
            "domain": self._domain_code(normalized_domain_id),
            "market": normalized_market,
        }

    def _matches_filter(self, deal: Dict[str, Any], filter_config: Dict[str, Any]) -> bool:
        min_discount = float(filter_config.get("min_discount", 0) or 0)
        max_discount = float(filter_config.get("max_discount", 100) or 100)
        min_price = float(filter_config.get("min_price", 0) or 0)
        max_price = float(filter_config.get("max_price", 1_000_000) or 1_000_000)
        min_rating = float(filter_config.get("min_rating", 0) or 0)

        return (
            min_discount <= deal["discount_percent"] <= max_discount
            and min_price <= deal["current_price"] <= max_price
            and deal["rating"] >= min_rating
        )

    async def search_deals(self, filter_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Search for deals using Keepa deals endpoint (returns real prices)."""
        from src.services.keepa_api import DealFilters

        domain_id = self._normalize_domain_id(filter_config.get("domain_id", 3))
        min_discount = int(filter_config.get("min_discount", 20) or 20)
        max_discount = int(filter_config.get("max_discount", 90) or 90)
        min_price = float(filter_config.get("min_price", 5) or 5)
        max_price = float(filter_config.get("max_price", 500) or 500)
        min_rating = float(filter_config.get("min_rating", 0) or 0)

        # Pass category filter to restrict deals to specific product categories
        categories = filter_config.get("categories")
        include_categories = None
        if categories:
            include_categories = [int(c) for c in categories]

        filters = DealFilters(
            page=0,
            domain_id=domain_id,
            include_categories=include_categories,
            min_discount=min_discount,
            max_discount=max_discount,
            min_price_cents=int(min_price * 100),
            max_price_cents=int(max_price * 100),
            min_reviews=0,
        )

        try:
            result = await get_keepa_client().search_deals(filters)
        except Exception as e:
            logger.warning("Deals API call failed: %s", e)
            return []

        raw_deals = result.get("deals", [])
        market = self._domain_code(domain_id).upper()

        deals: List[Dict[str, Any]] = []
        for raw in raw_deals:
            raw["domain_id"] = domain_id
            raw["domain"] = self._domain_code(domain_id)
            raw["market"] = market
            raw["source"] = "deals_api"
            normalized = self._normalize_deal(raw)
            if not self._matches_filter(normalized, filter_config):
                continue
            if min_rating > 0 and normalized.get("rating", 0) < min_rating:
                continue
            scored = self._score_deal(normalized)
            deals.append(scored)

        filtered = self.filter_spam(deals)
        filtered.sort(key=lambda x: x["deal_score"], reverse=True)
        max_deals = int(filter_config.get("max_asins", self.MAX_DEALS_PER_REPORT) or self.MAX_DEALS_PER_REPORT)
        return filtered[:max_deals]

    def _score_deal(self, deal: Dict[str, Any]) -> Dict[str, Any]:
        """Score a deal. Expects already-normalized input."""
        discount = deal.get("discount_percent", 0)
        rating = deal.get("rating", 0)
        sales_rank = deal.get("sales_rank", 100000)
        price = deal.get("current_price", 0)

        rating_score = (rating / 5.0) * 100 if rating > 0 else 0
        rank_score = max(0, 100 - (sales_rank / 2000))
        price_score = max(0, 100 - min(100, price))

        score = (discount * 0.5) + (rating_score * 0.35) + (rank_score * 0.1) + (
            price_score * 0.05
        )

        deal["deal_score"] = min(100, max(0, round(score, 2)))
        return deal

    def filter_spam(self, deals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out spam deals. Expects already-normalized input."""
        return [deal for deal in deals if self._is_valid_deal(deal)]

    def _is_valid_deal(self, deal: Dict[str, Any]) -> bool:
        if deal.get("rating", 0) < self.MIN_RATING:
            return False

        if deal.get("current_price", 0) < self.MIN_PRICE_THRESHOLD:
            return False

        title = deal.get("title", "").lower()
        for keyword in self.DROPSHIPPER_KEYWORDS:
            if keyword in title:
                return False

        discount = deal.get("discount_percent", 0)
        if discount > 80:
            return False

        return True

    def should_send_report(self, deals: List[Dict[str, Any]]) -> bool:
        valid_deals = self.filter_spam(deals)
        return len(valid_deals) >= self.MIN_DEALS_FOR_REPORT

    async def generate_report(
        self, deals: List[Dict[str, Any]], filter_name: str, filter_summary: str
    ) -> str:
        filtered_deals = self.filter_spam(deals)

        html = notification_service.format_deal_report_html(
            deals=filtered_deals, filter_name=filter_name, filter_summary=filter_summary
        )

        return html

    async def run_daily_search(self, filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        results = []

        for filter_config in filters:
            deals = await self.search_deals(filter_config)
            filtered = self.filter_spam(deals)

            # Index deals to Elasticsearch for search
            for deal in filtered:
                await self._index_deal_to_elasticsearch(deal)

            report_html = None
            if self.should_send_report(filtered):
                report_html = await self.generate_report(
                    filtered,
                    filter_config.get("name", "Daily Deals"),
                    f"Category: {filter_config.get('categories')}, "
                    f"Discount: {filter_config.get('min_discount')}-{filter_config.get('max_discount')}%, "
                    f"Price: {filter_config.get('min_price')}-{filter_config.get('max_price')}€",
                )

            results.append(
                {
                    "filter_id": filter_config.get("id"),
                    "filter_name": filter_config.get("name"),
                    "deals_found": len(filtered),
                    "should_send": self.should_send_report(filtered),
                    "top_deals": filtered[:5],
                    "report_html": report_html,
                }
            )

        return {
            "filters_processed": len(filters),
            "reports_ready": sum(1 for r in results if r["should_send"]),
            "results": results,
        }

    async def _index_deal_to_elasticsearch(self, deal: Dict[str, Any]) -> bool:
        """Index a single deal to Elasticsearch. Expects already-normalized input."""
        from datetime import datetime

        try:
            es_doc = {
                "asin": deal.get("asin", ""),
                "title": deal.get("title", ""),
                "description": deal.get("title", ""),
                "current_price": deal.get("current_price", 0),
                "original_price": deal.get("list_price", 0),
                "discount_percent": deal.get("discount_percent", 0),
                "rating": deal.get("rating", 0),
                "review_count": deal.get("reviews", 0),
                "sales_rank": deal.get("sales_rank", 0),
                "domain": deal.get("domain", "de"),
                "domain_id": deal.get("domain_id", 3),
                "market": deal.get("market", "DE"),
                "category": deal.get("category", "general"),
                "prime_eligible": deal.get("prime_eligible", False),
                "url": deal.get("url", ""),
                "deal_score": deal.get("deal_score", 0),
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": "deal_indexed",
                "source": deal.get("source", "product_api"),
            }
            return await es_service.index_deal_update(es_doc)
        except Exception as e:
            logger.error(f"Failed to index deal to ES: {e}")
            return False


deal_finder = DealFinderAgent()
