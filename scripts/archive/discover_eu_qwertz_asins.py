#!/usr/bin/env python3
"""
Discover a large ASIN seed list directly from Keepa endpoints.

This script avoids scraping Amazon HTML and instead uses:
- /query (Product Finder)
- /search with type=category
- /bestsellers

Output:
- JSON metadata file with run stats
- TXT file with comma-separated ASINs (seed file)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


ASIN_RE = re.compile(r"^B[A-Z0-9]{9}$")

MARKETS: Dict[str, Dict[str, Any]] = {
    "DE": {
        "domain_id": 3,
        "finder_terms": [
            "tastatur",
            "gaming tastatur",
            "mechanische tastatur",
            "wireless tastatur",
            "qwertz tastatur",
            "deutsche tastatur",
            "logitech tastatur",
            "cherry tastatur",
            "corsair tastatur",
            "razer tastatur",
            "keychron",
            "ducky tastatur",
            "steelseries tastatur",
            "hyperx tastatur",
        ],
        "category_terms": [
            "tastatur",
            "gaming tastatur",
            "computer tastatur",
            "mechanische tastatur",
        ],
    },
    "UK": {
        "domain_id": 2,
        "finder_terms": [
            "keyboard",
            "gaming keyboard",
            "mechanical keyboard",
            "wireless keyboard",
            "qwertz keyboard",
        ],
        "category_terms": [
            "keyboard",
            "gaming keyboard",
            "computer keyboard",
            "mechanical keyboard",
        ],
    },
    "FR": {
        "domain_id": 4,
        "finder_terms": [
            "clavier",
            "clavier gaming",
            "clavier mecanique",
            "clavier sans fil",
            "clavier qwertz",
        ],
        "category_terms": ["clavier", "clavier gaming", "clavier mecanique"],
    },
    "IT": {
        "domain_id": 8,
        "finder_terms": [
            "tastiera",
            "tastiera gaming",
            "tastiera meccanica",
            "tastiera wireless",
            "tastiera qwertz",
        ],
        "category_terms": ["tastiera", "tastiera gaming", "tastiera meccanica"],
    },
    "ES": {
        "domain_id": 9,
        "finder_terms": [
            "teclado",
            "teclado gaming",
            "teclado mecanico",
            "teclado inalambrico",
            "teclado qwertz",
        ],
        "category_terms": ["teclado", "teclado gaming", "teclado mecanico"],
    },
}

DEFAULT_BESTSELLER_RANGES = [0, 30, 60, 90, 120, 150, 180]


@dataclass
class Candidate:
    asin: str
    market: str
    domain_id: int
    source: str
    hint: str
    score: int


@dataclass
class RunStats:
    requests_total: int = 0
    requests_query: int = 0
    requests_search: int = 0
    requests_bestsellers: int = 0
    requests_product: int = 0
    request_errors: int = 0
    tokens_consumed_total: int = 0
    finder_asins: int = 0
    bestseller_asins: int = 0
    validated_checked: int = 0
    validated_ok: int = 0


def parse_asin_token(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    return token if ASIN_RE.match(token) else None


def extract_asins(obj: Any) -> List[str]:
    found: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for val in node.values():
                _walk(val)
            return
        if isinstance(node, (list, tuple, set)):
            for val in node:
                _walk(val)
            return
        asin = parse_asin_token(node)
        if asin:
            found.append(asin)

    _walk(obj)
    return found


def dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def http_get_json(url: str, timeout: int) -> Dict[str, Any]:
    import gzip

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", errors="ignore"))


def keepa_call(
    endpoint: str,
    api_key: str,
    domain_id: int,
    timeout: int,
    stats: RunStats,
    **params: Any,
) -> Dict[str, Any]:
    query_parts = [f"key={quote_plus(api_key)}", f"domain={domain_id}"]
    for key, value in params.items():
        query_parts.append(f"{quote_plus(str(key))}={quote_plus(str(value))}")

    url = f"https://api.keepa.com/{endpoint}/?{'&'.join(query_parts)}"

    stats.requests_total += 1
    if endpoint == "query":
        stats.requests_query += 1
    elif endpoint == "search":
        stats.requests_search += 1
    elif endpoint == "bestsellers":
        stats.requests_bestsellers += 1
    elif endpoint == "product":
        stats.requests_product += 1

    try:
        payload = http_get_json(url, timeout=timeout)
    except Exception:
        stats.request_errors += 1
        return {}

    stats.tokens_consumed_total += int(payload.get("tokensConsumed", 0) or 0)
    return payload


def add_candidates(
    pool: Dict[str, Candidate],
    asins: Iterable[str],
    market: str,
    domain_id: int,
    source: str,
    hint: str,
    score: int,
) -> int:
    added = 0
    for asin in asins:
        parsed = parse_asin_token(asin)
        if not parsed:
            continue

        existing = pool.get(parsed)
        if existing is None:
            pool[parsed] = Candidate(
                asin=parsed,
                market=market,
                domain_id=domain_id,
                source=source,
                hint=hint,
                score=score,
            )
            added += 1
            continue

        if score > existing.score:
            pool[parsed] = Candidate(
                asin=parsed,
                market=market,
                domain_id=domain_id,
                source=source,
                hint=hint,
                score=score,
            )

    return added


def discover_from_product_finder(
    api_key: str,
    market: str,
    market_cfg: Dict[str, Any],
    finder_pages: int,
    finder_per_page: int,
    min_price: float,
    max_price: float,
    timeout: int,
    pause_seconds: float,
    stats: RunStats,
    pool: Dict[str, Candidate],
) -> int:
    domain_id = int(market_cfg["domain_id"])
    terms = list(market_cfg["finder_terms"])

    before = len(pool)
    for term in terms:
        for page in range(max(1, finder_pages)):
            selection: Dict[str, Any] = {
                "title": term,
                "title_flag": 0,
                "page": page,
                "perPage": max(10, finder_per_page),
            }
            if min_price > 0:
                selection["current_NEW_gte"] = int(min_price * 100)
            if max_price > 0:
                selection["current_NEW_lte"] = int(max_price * 100)

            payload = keepa_call(
                endpoint="query",
                api_key=api_key,
                domain_id=domain_id,
                timeout=timeout,
                stats=stats,
                selection=json.dumps(selection, separators=(",", ":")),
            )
            asin_list = payload.get("asinList") if isinstance(payload, dict) else None
            asins = dedupe_preserve_order(extract_asins(asin_list or []))

            if not asins:
                break

            stats.finder_asins += len(asins)
            add_candidates(
                pool=pool,
                asins=asins,
                market=market,
                domain_id=domain_id,
                source="query",
                hint=f"{term}#p{page}",
                score=3,
            )

            if pause_seconds > 0:
                time.sleep(pause_seconds)

    return len(pool) - before


def parse_category_ids(payload: Dict[str, Any]) -> List[int]:
    ids: List[int] = []
    categories = payload.get("categories")

    if isinstance(categories, dict):
        for key in categories.keys():
            try:
                value = int(str(key))
            except Exception:
                continue
            if value > 0:
                ids.append(value)

    if isinstance(categories, list):
        for item in categories:
            if not isinstance(item, dict):
                continue
            raw = item.get("catId") or item.get("id")
            try:
                value = int(raw)
            except Exception:
                continue
            if value > 0:
                ids.append(value)

    return dedupe_preserve_order(ids)


def discover_categories(
    api_key: str,
    market_cfg: Dict[str, Any],
    timeout: int,
    stats: RunStats,
) -> List[int]:
    domain_id = int(market_cfg["domain_id"])
    terms = list(market_cfg["category_terms"])

    found_ids: List[int] = []
    for term in terms:
        payload = keepa_call(
            endpoint="search",
            api_key=api_key,
            domain_id=domain_id,
            timeout=timeout,
            stats=stats,
            type="category",
            term=term,
        )
        found_ids.extend(parse_category_ids(payload))

    return dedupe_preserve_order(found_ids)


def discover_from_bestsellers(
    api_key: str,
    market: str,
    market_cfg: Dict[str, Any],
    category_ids: List[int],
    bestseller_ranges: List[int],
    max_categories: int,
    timeout: int,
    pause_seconds: float,
    stats: RunStats,
    pool: Dict[str, Candidate],
) -> int:
    domain_id = int(market_cfg["domain_id"])
    before = len(pool)

    for category_id in category_ids[: max(1, max_categories)]:
        for range_offset in bestseller_ranges:
            payload = keepa_call(
                endpoint="bestsellers",
                api_key=api_key,
                domain_id=domain_id,
                timeout=timeout,
                stats=stats,
                category=category_id,
                range=max(0, int(range_offset)),
            )

            asins = dedupe_preserve_order(
                extract_asins(
                    payload.get("bestSellersList")
                    if isinstance(payload, dict)
                    else payload
                )
            )

            if not asins:
                continue

            stats.bestseller_asins += len(asins)
            add_candidates(
                pool=pool,
                asins=asins,
                market=market,
                domain_id=domain_id,
                source="bestsellers",
                hint=f"cat={category_id},range={range_offset}",
                score=2,
            )

            if pause_seconds > 0:
                time.sleep(pause_seconds)

    return len(pool) - before


def parse_current_price(current: Any) -> Optional[float]:
    if isinstance(current, list):
        for idx in (0, 7, 1):
            if (
                len(current) > idx
                and isinstance(current[idx], (int, float))
                and current[idx] > 0
            ):
                return round(float(current[idx]) / 100.0, 2)
    return None


def validate_asins(
    api_key: str,
    domain_id: int,
    asins: List[str],
    validate_count: int,
    validate_batch_size: int,
    min_price: float,
    timeout: int,
    pause_seconds: float,
    stats: RunStats,
) -> Set[str]:
    if validate_count <= 0 or not asins:
        return set()

    target = asins[: max(1, validate_count)]
    batch_size = max(1, min(100, validate_batch_size))
    valid: Set[str] = set()

    for i in range(0, len(target), batch_size):
        batch = target[i : i + batch_size]
        payload = keepa_call(
            endpoint="product",
            api_key=api_key,
            domain_id=domain_id,
            timeout=timeout,
            stats=stats,
            asin=",".join(batch),
        )

        products = payload.get("products") if isinstance(payload, dict) else None
        if not isinstance(products, list):
            continue

        for product in products:
            if not isinstance(product, dict):
                continue

            asin = parse_asin_token(product.get("asin"))
            if not asin:
                continue

            title = str(product.get("title") or "").strip()
            if len(title) < 4:
                continue

            price = parse_current_price(product.get("current"))
            if price is None or price < min_price:
                continue

            valid.add(asin)

        stats.validated_checked += len(batch)
        stats.validated_ok = len(valid)

        if pause_seconds > 0:
            time.sleep(pause_seconds)

    return valid


def write_outputs(
    output_json: Path,
    output_txt: Path,
    selected_asins: List[str],
    selected_records: List[Candidate],
    stats: RunStats,
    runtime_meta: Dict[str, Any],
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_txt.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": asdict(stats),
        "run": runtime_meta,
        "asins": selected_asins,
        "records": [asdict(record) for record in selected_records],
    }

    output_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    output_txt.write_text(",".join(selected_asins), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover large keyboard ASIN seed lists from Keepa endpoints."
    )
    parser.add_argument("--api-key", default=os.getenv("KEEPA_API_KEY", ""))
    parser.add_argument("--markets", default="UK,FR,IT,ES")
    parser.add_argument("--seed-limit", type=int, default=1000)

    parser.add_argument("--finder-pages", type=int, default=8)
    parser.add_argument("--finder-per-page", type=int, default=500)
    parser.add_argument("--max-categories", type=int, default=80)
    parser.add_argument("--bestseller-ranges", default="0,30,60,90,120,150,180")

    parser.add_argument("--min-price", type=float, default=8.0)
    parser.add_argument("--max-price", type=float, default=800.0)

    parser.add_argument(
        "--validate-count",
        type=int,
        default=0,
        help="Optional: validate first N ASINs with /product endpoint (token heavy).",
    )
    parser.add_argument("--validate-batch-size", type=int, default=40)

    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause-ms", type=int, default=100)

    parser.add_argument("--output-json", default="data/seed_asins_eu_qwertz.json")
    parser.add_argument("--output-txt", default="data/seed_asins_eu_qwertz.txt")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.api_key:
        print("ERROR: Missing --api-key and KEEPA_API_KEY.", file=sys.stderr)
        return 2

    selected_markets = [
        m.strip().upper() for m in str(args.markets).split(",") if m.strip()
    ]
    invalid = [m for m in selected_markets if m not in MARKETS]
    if invalid:
        print(f"ERROR: Unsupported markets: {', '.join(invalid)}", file=sys.stderr)
        return 2

    bestseller_ranges: List[int] = []
    for token in str(args.bestseller_ranges).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            bestseller_ranges.append(int(token))
        except Exception:
            pass
    if not bestseller_ranges:
        bestseller_ranges = list(DEFAULT_BESTSELLER_RANGES)

    pause_seconds = max(0.0, int(args.pause_ms) / 1000.0)
    stats = RunStats()

    pool: Dict[str, Candidate] = {}
    category_map: Dict[str, List[int]] = {}

    print(f"Discovering ASINs via Keepa for markets: {', '.join(selected_markets)}")
    for market in selected_markets:
        cfg = MARKETS[market]

        added_finder = discover_from_product_finder(
            api_key=args.api_key,
            market=market,
            market_cfg=cfg,
            finder_pages=max(1, int(args.finder_pages)),
            finder_per_page=max(10, int(args.finder_per_page)),
            min_price=max(0.0, float(args.min_price)),
            max_price=max(0.0, float(args.max_price)),
            timeout=max(5, int(args.timeout)),
            pause_seconds=pause_seconds,
            stats=stats,
            pool=pool,
        )

        category_ids = discover_categories(
            api_key=args.api_key,
            market_cfg=cfg,
            timeout=max(5, int(args.timeout)),
            stats=stats,
        )
        category_map[market] = category_ids

        added_best = discover_from_bestsellers(
            api_key=args.api_key,
            market=market,
            market_cfg=cfg,
            category_ids=category_ids,
            bestseller_ranges=bestseller_ranges,
            max_categories=max(1, int(args.max_categories)),
            timeout=max(5, int(args.timeout)),
            pause_seconds=pause_seconds,
            stats=stats,
            pool=pool,
        )

        print(
            f"  {market}: +{added_finder} from query, +{added_best} from bestsellers, "
            f"categories={len(category_ids)}"
        )

    ordered_candidates = list(pool.values())
    ordered_asins = [item.asin for item in ordered_candidates]

    if args.validate_count > 0 and ordered_asins:
        primary_domain = int(MARKETS[selected_markets[0]]["domain_id"])
        valid = validate_asins(
            api_key=args.api_key,
            domain_id=primary_domain,
            asins=ordered_asins,
            validate_count=max(1, int(args.validate_count)),
            validate_batch_size=max(1, int(args.validate_batch_size)),
            min_price=max(0.0, float(args.min_price)),
            timeout=max(5, int(args.timeout)),
            pause_seconds=pause_seconds,
            stats=stats,
        )
        if valid:
            ordered_asins = [asin for asin in ordered_asins if asin in valid] + [
                asin for asin in ordered_asins if asin not in valid
            ]
            by_asin = {item.asin: item for item in ordered_candidates}
            ordered_candidates = [
                by_asin[asin] for asin in ordered_asins if asin in by_asin
            ]

    seed_limit = max(1, int(args.seed_limit))
    selected_asins = ordered_asins[:seed_limit]
    selected_records = ordered_candidates[:seed_limit]

    runtime_meta: Dict[str, Any] = {
        "markets": selected_markets,
        "seed_limit": seed_limit,
        "finder_pages": int(args.finder_pages),
        "finder_per_page": int(args.finder_per_page),
        "max_categories": int(args.max_categories),
        "bestseller_ranges": bestseller_ranges,
        "min_price": float(args.min_price),
        "max_price": float(args.max_price),
        "validate_count": int(args.validate_count),
        "category_ids_by_market": category_map,
        "discovered_unique_total": len(ordered_asins),
    }

    output_json = Path(args.output_json)
    output_txt = Path(args.output_txt)
    write_outputs(
        output_json=output_json,
        output_txt=output_txt,
        selected_asins=selected_asins,
        selected_records=selected_records,
        stats=stats,
        runtime_meta=runtime_meta,
    )

    print(f"Unique discovered ASINs: {len(ordered_asins)}")
    print(f"Selected ASINs: {len(selected_asins)}")
    print(
        f"Requests: {stats.requests_total}, errors: {stats.request_errors}, tokens: {stats.tokens_consumed_total}"
    )
    print(f"Wrote JSON: {output_json}")
    print(f"Wrote TXT : {output_txt}")

    if not selected_asins:
        print(
            "WARNING: No ASINs discovered. Check API key, token status, and market filters."
        )
        return 1

    preview = ",".join(selected_asins[:20])
    print(f"Preview (first 20): {preview}")

    if len(selected_asins) < seed_limit:
        print(
            f"WARNING: Requested {seed_limit} seeds but only {len(selected_asins)} found. "
            "Try more markets/pages/categories."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
