#!/usr/bin/env python3
"""
QWERTZ Keyboard ASIN Discovery via Keepa /search API
=====================================================
Discover QWERTZ keyboards across EU markets using Keepa search + product APIs.
Clone of discover_flat_keyboards.py template, adapted for QWERTZ layout detection.

Usage:
    python scripts/discover_qwertz_keyboards_keepa.py
    python scripts/discover_qwertz_keyboards_keepa.py --max-per-market 200

Output:
    data/keepa_search_qwertz.csv
    data/keepa_search_qwertz.json
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import importlib.util

spec = importlib.util.spec_from_file_location(
    "keepa_client_module",
    Path(__file__).parent.parent / "src" / "services" / "keepa_client.py",
)
keepa_client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keepa_client_module)
KeepaClient = keepa_client_module.KeepaClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("discover_qwertz_keepa")

DOMAINS = {
    "DE": 3,
    "UK": 2,
    "FR": 4,
    "IT": 8,
    "ES": 9,
}

SEARCH_KEYWORDS = {
    "DE": [
        "tastatur",
        "mechanische tastatur",
        "gaming tastatur",
        "kabellose tastatur",
        "bluetooth tastatur",
        "cherry tastatur",
        "logitech tastatur",
        "corsair tastatur",
        "razer tastatur",
        "keychron",
        "ergonomische tastatur",
        "mini tastatur",
        "60% tastatur",
        "tkl tastatur",
        "usb tastatur",
    ],
    "UK": [
        "QWERTZ keyboard",
        "german keyboard",
        "german layout keyboard",
        "deutsche tastatur",
        "cherry keyboard german",
        "logitech QWERTZ",
        "mechanical keyboard german",
        "wireless keyboard german layout",
        "gaming keyboard QWERTZ",
    ],
    "FR": [
        "clavier qwertz",
        "clavier allemand",
        "clavier layout allemand",
        "clavier mecanique qwertz",
        "tastatur",
        "clavier cherry allemand",
        "clavier gaming allemand",
        "clavier sans fil allemand",
    ],
    "IT": [
        "tastiera qwertz",
        "tastiera tedesca",
        "tastiera layout tedesco",
        "tastiera meccanica qwertz",
        "tastatur",
        "tastiera cherry tedesca",
        "tastiera gaming tedesca",
        "tastiera wireless tedesca",
    ],
    "ES": [
        "teclado qwertz",
        "teclado aleman",
        "teclado layout aleman",
        "teclado mecanico qwertz",
        "tastatur",
        "teclado cherry aleman",
        "teclado gaming aleman",
        "teclado inalambrico aleman",
    ],
}

MAX_PAGES = 3

stats = {
    "searches": 0,
    "asins_found": 0,
    "products_fetched": 0,
    "validated": 0,
    "errors": 0,
    "tokens_consumed": 0,
}


def get_api_key() -> str:
    api_key = os.environ.get("KEEPA_API_KEY", "")
    if not api_key:
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("KEEPA_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return api_key


async def check_token_budget(client: KeepaClient):
    """Check available Keepa tokens before starting."""
    try:
        import httpx

        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://api.keepa.com/token",
                params={"key": client.api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                tokens = data.get("tokensLeft", 0)
                refill = data.get("refillIn", 0)
                log.info(f"Token budget: {tokens} tokens left, refill in {refill}ms")
                return tokens
    except Exception as e:
        log.warning(f"Could not check token budget: {e}")
    return None


async def search_keyword(
    client: KeepaClient, keyword: str, domain_id: int, page: int = 1
) -> list[str]:
    """Search for products and return ASINs."""
    try:
        response = await client.search_products(
            search_term=keyword,
            domain_id=domain_id,
            page=page,
        )
        stats["searches"] += 1
        stats["tokens_consumed"] += response.get("metadata", {}).get(
            "tokens_consumed", 0
        )

        products = response.get("raw", {}).get("products", [])
        asins = [p.get("asin") for p in products if p.get("asin")]
        stats["asins_found"] += len(asins)

        return asins
    except Exception as e:
        log.warning(f"Search failed for '{keyword}' on domain {domain_id}: {e}")
        stats["errors"] += 1
        return []


async def validate_batch(
    client: KeepaClient,
    asins: list[str],
    domain_id: int,
    market: str,
    batch_size: int = 50,
) -> list[dict]:
    """Validate ASINs via Keepa /product in batches."""
    validated = []

    for i in range(0, len(asins), batch_size):
        batch = asins[i : i + batch_size]

        try:
            response = await client.get_products(batch, domain_id)
            stats["products_fetched"] += len(batch)
            stats["tokens_consumed"] += response.get("metadata", {}).get(
                "tokens_consumed", 0
            )

            products = response.get("raw", {}).get("products", [])

            for product in products:
                asin = product.get("asin", "")
                title = product.get("title", "")
                if not title:
                    continue

                brand = product.get("brand", "")
                csv_data = product.get("csv") or []
                ean_list = product.get("eanList") or []

                # Extract prices
                def get_price(idx):
                    if (
                        len(csv_data) > idx
                        and csv_data[idx]
                        and len(csv_data[idx]) >= 2
                        and csv_data[idx][-1] > 0
                    ):
                        return csv_data[idx][-1] / 100.0
                    return None

                new_price = get_price(1)
                used_price = get_price(2)
                amazon_price = get_price(0)

                # At least some price must exist
                if new_price is None and used_price is None and amazon_price is None:
                    continue

                validated.append(
                    {
                        "asin": asin,
                        "domain_id": domain_id,
                        "market": market,
                        "title": title[:200],
                        "new_price": new_price or amazon_price,
                        "used_price": used_price,
                        "brand": brand,
                        "ean": ean_list[0] if ean_list else "",
                        "source": "keepa_search",
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                stats["validated"] += 1

        except Exception as e:
            log.warning(f"Batch validation error on {market}: {e}")
            stats["errors"] += 1

        await asyncio.sleep(2)

    return validated


async def discover_market(
    client: KeepaClient,
    market: str,
    domain_id: int,
    max_results: int = 100,
) -> list[dict]:
    """Discover QWERTZ keyboards for a single market."""
    log.info(f"\nDiscovering {market} (domain={domain_id})")

    keywords = SEARCH_KEYWORDS.get(market, [])
    all_asins = set()

    for keyword in keywords:
        log.info(f"  Searching: '{keyword}'")

        for page in range(1, MAX_PAGES + 1):
            asins = await search_keyword(client, keyword, domain_id, page)
            new_count = len(asins - all_asins) if isinstance(asins, set) else len(
                [a for a in asins if a not in all_asins]
            )
            all_asins.update(asins)

            log.info(f"    Page {page}: +{new_count} new (total: {len(all_asins)})")

            if len(asins) == 0:
                break

            await asyncio.sleep(2)

    log.info(f"  {len(all_asins)} unique ASINs found, validating...")

    asin_list = list(all_asins)
    if len(asin_list) > max_results * 3:
        asin_list = asin_list[: max_results * 3]

    validated = await validate_batch(client, asin_list, domain_id, market)

    log.info(f"  {len(validated)} validated for {market}")
    return validated[:max_results]


async def discover_all(client: KeepaClient, max_per_market: int = 100) -> list[dict]:
    """Discover across all markets."""
    results = []

    for market, domain_id in DOMAINS.items():
        validated = await discover_market(client, market, domain_id, max_per_market)
        results.extend(validated)
        await asyncio.sleep(5)

    return results


def write_output(results: list[dict], output_path: Path):
    """Write results to CSV and JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "asin",
        "domain_id",
        "market",
        "title",
        "new_price",
        "used_price",
        "brand",
        "ean",
        "source",
        "validated_at",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    log.info(f"Saved {len(results)} records to {output_path}")

    # JSON output
    json_path = output_path.with_suffix(".json")
    by_market = {}
    for r in results:
        by_market.setdefault(r["market"], []).append(r)

    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(results),
            "markets": list(DOMAINS.keys()),
            "by_market_count": {m: len(v) for m, v in by_market.items()},
            "stats": stats,
        },
        "all_asins": list(dict.fromkeys(r["asin"] for r in results)),
        "by_market": by_market,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    log.info(f"Saved JSON to {json_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Discover QWERTZ keyboard ASINs via Keepa /search API"
    )
    parser.add_argument("--max-per-market", type=int, default=100)
    parser.add_argument("--output", type=str, default="data/keepa_search_qwertz.csv")
    args = parser.parse_args()

    api_key = get_api_key()
    if not api_key:
        log.error("KEEPA_API_KEY not set. Set in .env or environment.")
        sys.exit(1)

    client = KeepaClient(api_key=api_key)

    log.info("QWERTZ Keyboard Discovery via Keepa")
    log.info(f"  Markets: {', '.join(DOMAINS.keys())}")
    log.info(
        f"  Keywords: {sum(len(kws) for kws in SEARCH_KEYWORDS.values())} total"
    )
    log.info(f"  Pages/keyword: {MAX_PAGES}")
    log.info(f"  Max/market: {args.max_per_market}")

    start = time.time()

    # Check tokens
    asyncio.run(check_token_budget(client))

    results = asyncio.run(discover_all(client, args.max_per_market))

    elapsed = time.time() - start

    output_path = Path(__file__).parent.parent / args.output
    write_output(results, output_path)

    log.info(f"\nDiscovery complete in {elapsed / 60:.1f} min")
    log.info(f"  Total validated: {len(results)}")
    log.info(f"  Stats: {stats}")

    # Check tokens after
    asyncio.run(check_token_budget(client))


if __name__ == "__main__":
    main()
