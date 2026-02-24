#!/usr/bin/env python3
"""
Bestseller + Product Finder Keyboard Discovery
================================================
Discovers keyboard ASINs via:
  1. Keepa Category Search → Browse Node IDs for "keyboard"
  2. Keepa Bestsellers → Top ASINs per category per market
  3. Keepa Product Finder → Filtered ASINs (active, reviewed keyboards)

Deduplicates against existing ASINs in keyboard_targets_1000.csv.

Output: data/keepa_bestsellers_keyboards.csv

Usage:
    python scripts/discover_bestseller_keyboards.py
    python scripts/discover_bestseller_keyboards.py --dry-run
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
log = logging.getLogger("discover_bestsellers")

PROJECT_ROOT = Path(__file__).parent.parent

DOMAINS = {
    "DE": 3,
    "UK": 2,
    "FR": 4,
    "IT": 8,
    "ES": 9,
}

# Fallback keyboard category IDs per market (Amazon browse nodes)
# These are common keyboard categories; search_categories will find more
FALLBACK_KEYBOARD_CATEGORIES = {
    3: [340843031],    # DE: Computer-Tastaturen
    2: [340831031],    # UK: Computer Keyboards
    4: [340843031],    # FR
    8: [340843031],    # IT
    9: [340843031],    # ES
}


def get_api_key() -> str:
    api_key = os.environ.get("KEEPA_API_KEY", "")
    if not api_key:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("KEEPA_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return api_key


def load_existing_asins() -> set:
    """Load already-known ASINs to deduplicate against."""
    existing = set()
    targets_path = PROJECT_ROOT / "data" / "keyboard_targets_1000.csv"
    if targets_path.exists():
        with open(targets_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                asin = row.get("asin", "").strip().upper()
                if asin:
                    existing.add(asin)
        log.info(f"Loaded {len(existing)} existing ASINs for dedup")

    # Also check other source files
    for src_file in [
        "keepa_search_qwertz.csv",
        "amazon_asins_raw.csv",
        "seed_best_sellers.csv",
    ]:
        src_path = PROJECT_ROOT / "data" / src_file
        if src_path.exists():
            with open(src_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    asin = row.get("asin", "").strip().upper()
                    if asin:
                        existing.add(asin)

    # Check JSON seed file
    json_path = PROJECT_ROOT / "data" / "seed_asins_eu_qwertz.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text())
            asin_list = data.get("all_asins", []) or data.get("asins", [])
            if not asin_list and isinstance(data, list):
                asin_list = data
            for asin in asin_list:
                existing.add(asin.strip().upper())
        except Exception:
            pass

    log.info(f"Total existing ASINs for dedup: {len(existing)}")
    return existing


async def discover_categories(client: KeepaClient) -> dict:
    """Step 1: Search for keyboard category IDs across all markets."""
    log.info("Step 1: Searching for keyboard categories...")
    categories_by_domain = {}

    for market, domain_id in DOMAINS.items():
        try:
            response = await client.search_categories("keyboard", domain_id)
            cats = response.get("categories", {})
            if cats:
                # Take top 3 most relevant categories
                cat_ids = list(cats.keys())[:3]
                categories_by_domain[domain_id] = [int(c) for c in cat_ids]
                cat_names = [cats[c].get("name", c) for c in cat_ids[:3]]
                log.info(f"  {market}: Found {len(cat_ids)} categories: {cat_names}")
            else:
                categories_by_domain[domain_id] = FALLBACK_KEYBOARD_CATEGORIES.get(
                    domain_id, []
                )
                log.info(f"  {market}: No categories found, using fallback")
        except Exception as e:
            log.warning(f"  {market}: Category search failed: {e}")
            categories_by_domain[domain_id] = FALLBACK_KEYBOARD_CATEGORIES.get(
                domain_id, []
            )

        # Token-aware pause between markets
        remaining = getattr(client, 'rate_limit_remaining', 100)
        if remaining < 10:
            log.info(f"  Tokens low ({remaining}), waiting 30s...")
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(2)

    return categories_by_domain


async def discover_bestsellers(
    client: KeepaClient, categories_by_domain: dict
) -> list[dict]:
    """Step 2: Fetch bestseller ASINs for each category per market."""
    log.info("Step 2: Fetching bestseller ASINs...")
    discovered = []

    for market, domain_id in DOMAINS.items():
        cat_ids = categories_by_domain.get(domain_id, [])
        if not cat_ids:
            log.info(f"  {market}: No categories, skipping")
            continue

        for cat_id in cat_ids:
            try:
                result = await client.get_bestsellers(domain_id, cat_id)
                asin_list = result.get("raw", {}).get("bestSellersList") or []
                tokens = result.get("metadata", {}).get("tokens_consumed", 0)

                for asin in asin_list:
                    if isinstance(asin, str) and len(asin) == 10:
                        discovered.append(
                            {
                                "asin": asin.upper(),
                                "market": market,
                                "domain_id": domain_id,
                                "source": "bestseller",
                                "category_id": cat_id,
                            }
                        )

                log.info(
                    f"  {market} cat={cat_id}: {len(asin_list)} ASINs ({tokens} tokens)"
                )
            except Exception as e:
                log.warning(f"  {market} cat={cat_id}: Bestseller fetch failed: {e}")
                if "rate limit" in str(e).lower():
                    log.info("    Rate limited — extra 30s pause")
                    await asyncio.sleep(30)

            # Token-aware pause between categories
            remaining = getattr(client, 'rate_limit_remaining', 100)
            if remaining < 10:
                log.info(f"  Tokens low ({remaining}), waiting 30s...")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(2)

        await asyncio.sleep(5)  # More breathing room between markets

    return discovered


async def discover_product_finder(
    client: KeepaClient, categories_by_domain: dict
) -> list[dict]:
    """Step 3: Use Product Finder for targeted keyboard search."""
    log.info("Step 3: Product Finder targeted search...")
    discovered = []

    for market, domain_id in DOMAINS.items():
        cat_ids = categories_by_domain.get(domain_id, [])
        root_cat = cat_ids[0] if cat_ids else None
        if root_cat is None:
            continue

        try:
            parms = {
                "productType": [0],  # Standard products
                "rootCategory": root_cat,
                "salesRankRange": [1, 100000],
                "hasReviews": True,
            }
            result = await client.product_finder(domain_id, parms)
            asin_list = result.get("raw", {}).get("asinList") or []
            tokens = result.get("metadata", {}).get("tokens_consumed", 0)

            for asin in asin_list:
                if isinstance(asin, str) and len(asin) == 10:
                    discovered.append(
                        {
                            "asin": asin.upper(),
                            "market": market,
                            "domain_id": domain_id,
                            "source": "product_finder",
                            "category_id": root_cat,
                        }
                    )

            log.info(
                f"  {market}: Product Finder returned {len(asin_list)} ASINs ({tokens} tokens)"
            )
        except Exception as e:
            log.warning(f"  {market}: Product Finder failed: {e}")
            if "rate limit" in str(e).lower():
                log.info("    Rate limited — extra 30s pause")
                await asyncio.sleep(30)

        # Token-aware pause between markets
        remaining = getattr(client, 'rate_limit_remaining', 100)
        if remaining < 10:
            log.info(f"  Tokens low ({remaining}), waiting 30s...")
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(3)

    return discovered


async def run(args):
    api_key = get_api_key()
    if not api_key:
        log.error("KEEPA_API_KEY not set. Export it or add to .env file.")
        sys.exit(1)

    client = KeepaClient(api_key=api_key)

    # Load existing ASINs for dedup
    existing_asins = load_existing_asins()

    # Step 1: Discover categories
    categories = await discover_categories(client)

    # Step 2: Bestsellers
    bestseller_asins = await discover_bestsellers(client, categories)
    log.info(f"Bestsellers: {len(bestseller_asins)} total ASINs discovered")

    # Step 3: Product Finder
    finder_asins = await discover_product_finder(client, categories)
    log.info(f"Product Finder: {len(finder_asins)} total ASINs discovered")

    # Combine and dedup
    all_discovered = bestseller_asins + finder_asins
    seen = {}
    for entry in all_discovered:
        asin = entry["asin"]
        if asin not in seen:
            seen[asin] = entry

    total_unique = len(seen)
    new_asins = {k: v for k, v in seen.items() if k not in existing_asins}
    log.info(f"Total unique: {total_unique}, New (not in existing): {len(new_asins)}")

    if args.dry_run:
        log.info("DRY RUN — not writing output file")
        return

    # Write output
    output_path = PROJECT_ROOT / "data" / "keepa_bestsellers_keyboards.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["asin", "market", "domain_id", "source", "category_id"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(new_asins.values())

    log.info(f"Saved {len(new_asins)} new ASINs to {output_path}")

    # Also append new ASINs to seed JSON for next orchestrator run
    seed_path = PROJECT_ROOT / "data" / "seed_asins_eu_qwertz.json"
    try:
        if seed_path.exists():
            seed_data = json.loads(seed_path.read_text())
        else:
            seed_data = {"all_asins": []}

        existing_seed = set(seed_data.get("all_asins", []))
        added = 0
        for asin in new_asins:
            if asin not in existing_seed:
                seed_data.setdefault("all_asins", []).append(asin)
                added += 1

        seed_data["_last_discovery"] = datetime.now(timezone.utc).isoformat()
        seed_data["_discovery_stats"] = {
            "bestseller_count": len(bestseller_asins),
            "finder_count": len(finder_asins),
            "new_unique": len(new_asins),
        }

        seed_path.write_text(json.dumps(seed_data, indent=2, ensure_ascii=False))
        log.info(f"Added {added} new ASINs to {seed_path}")
    except Exception as e:
        log.warning(f"Could not update seed JSON: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Discover keyboard ASINs via Keepa Bestsellers + Product Finder"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write output files, just show what would be found",
    )
    args = parser.parse_args()

    log.info("Keepa Bestseller + Product Finder Discovery")
    log.info(f"  Markets: {', '.join(DOMAINS.keys())}")

    start = time.time()
    asyncio.run(run(args))
    elapsed = time.time() - start
    log.info(f"Complete in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
