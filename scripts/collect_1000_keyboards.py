#!/usr/bin/env python3
"""
Keyboard Collection Orchestrator — 1000+ Layout-Mismatch Targets
================================================================
Merges ASINs from multiple sources, validates via Keepa /product,
detects keyboard layout via multi-layer heuristics, and outputs
a final CSV of layout-mismatch targets.

Sources:
    1. data/keepa_search_qwertz.csv (Keepa /search discovery)
    2. data/amazon_asins_raw.csv (Amazon scraper)
    3. data/seed_asins_eu_qwertz.json (static ASIN pool)
    4. data/chrome_extension_asins.csv (optional, manual)

Output:
    data/keyboard_targets_1000.csv
    data/keyboard_targets_1000.json

Usage:
    python scripts/collect_1000_keyboards.py
    python scripts/collect_1000_keyboards.py --skip-validation
    python scripts/collect_1000_keyboards.py --domains UK,FR,IT,ES
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
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
log = logging.getLogger("collect_keyboards")

PROJECT_ROOT = Path(__file__).parent.parent

DOMAINS = {
    "DE": 3,
    "UK": 2,
    "FR": 4,
    "IT": 8,
    "ES": 9,
}

# --- Layout Detection: Multi-Layer ---

# Layer 1: Title keyword matching
LAYOUT_TITLE_KEYWORDS = {
    "qwertz": [
        "qwertz",
        "deutsch",
        "german layout",
        "iso-de",
        "german keyboard",
        "deutsche tastatur",
        "tastatur qwertz",
        "de layout",
        "germanisches layout",
        "deutsches layout",
    ],
    "azerty": [
        "azerty",
        "french layout",
        "clavier francais",
        "clavier azerty",
        "fr layout",
        "disposition francaise",
    ],
    "qwerty_uk": [
        "uk layout",
        "british layout",
        "qwerty uk",
        "english uk",
        "gb layout",
    ],
    "qwerty_it": [
        "italian layout",
        "italiano",
        "it layout",
        "tastiera italiana",
    ],
    "qwerty_es": [
        "spanish layout",
        "espanol",
        "es layout",
        "teclado espanol",
    ],
}

# Layer 2: Known QWERTZ brand+model combinations
KNOWN_QWERTZ_MODELS = [
    "cherry kc 1000",
    "cherry kc 6000",
    "cherry stream",
    "cherry g80",
    "cherry g84",
    "cherry g86",
    "cherry jk",
    "cherry mx board",
    "cherry b.unlimited",
    "cherry dw 9100",
    "cherry dw 5100",
    "logitech k120 de",
    "logitech k400 de",
    "logitech k380 de",
    "logitech mk270 de",
    "logitech mk295 de",
    "logitech mk545 de",
    "logitech g413 de",
    "logitech g512 de",
    "logitech g pro de",
    "perixx periboard",
    "microsoft sculpt de",
    "microsoft ergonomic de",
    "microsoft surface de",
    "corsair k70 de",
    "corsair k95 de",
    "corsair k65 de",
    "razer blackwidow de",
    "razer huntsman de",
    "razer ornata de",
    "steelseries apex de",
    "hyperx alloy de",
    "ducky one 2 de",
    "keychron k2 de",
    "keychron q1 de",
    "keychron k8 de",
    "fortez kb1",
    "hama kc-200",
    "hama kc-500",
    "logilink id0194",
    "trust ody",
    "medion akoya",
]

# Expected layout per market
EXPECTED_LAYOUT = {
    "DE": "qwertz",
    "UK": "qwerty_uk",
    "FR": "azerty",
    "IT": "qwerty_it",
    "ES": "qwerty_es",
}

stats = {
    "sources": {},
    "total_raw": 0,
    "after_dedup": 0,
    "validated": 0,
    "confirmed_mismatch": 0,
    "suspected_mismatch": 0,
    "unknown": 0,
    "tokens_consumed": 0,
    "errors": 0,
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


# ===== Phase 3a: Merge =====


def load_keepa_search(path: Path) -> dict[str, dict]:
    """Load ASINs from keepa_search_qwertz.csv."""
    asins = {}
    if not path.exists():
        log.warning(f"  Not found: {path}")
        return asins

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asin = row.get("asin", "").strip().upper()
            if asin and len(asin) == 10:
                asins[asin] = {
                    "asin": asin,
                    "title": row.get("title", ""),
                    "market": row.get("market", ""),
                    "domain_id": int(row.get("domain_id", 0)),
                    "new_price": _parse_float(row.get("new_price")),
                    "used_price": _parse_float(row.get("used_price")),
                    "brand": row.get("brand", ""),
                    "ean": row.get("ean", ""),
                    "source": "keepa_search",
                }

    log.info(f"  keepa_search: {len(asins)} ASINs")
    return asins


def load_amazon_scrape(path: Path) -> dict[str, dict]:
    """Load ASINs from amazon_asins_raw.csv."""
    asins = {}
    if not path.exists():
        log.warning(f"  Not found: {path}")
        return asins

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asin = row.get("asin", "").strip().upper()
            if asin and len(asin) == 10:
                asins[asin] = {
                    "asin": asin,
                    "title": row.get("title", ""),
                    "market": row.get("market", ""),
                    "new_price": _parse_float(row.get("price")),
                    "brand": "",
                    "source": "amazon_scrape",
                }

    log.info(f"  amazon_scrape: {len(asins)} ASINs")
    return asins


def load_static_pool(json_path: Path, txt_path: Path) -> dict[str, dict]:
    """Load ASINs from seed_asins_eu_qwertz.json or .txt."""
    asins = {}

    if json_path.exists():
        try:
            data = json.loads(json_path.read_text())
            asin_list = data.get("all_asins", []) or data.get("asins", [])
            if not asin_list and isinstance(data, list):
                asin_list = data
            for asin in asin_list:
                asin = asin.strip().upper()
                if asin and len(asin) == 10:
                    asins[asin] = {
                        "asin": asin,
                        "title": "",
                        "market": "",
                        "source": "static_pool",
                    }
            log.info(f"  static_pool (json): {len(asins)} ASINs")
            return asins
        except Exception as e:
            log.warning(f"  Error reading {json_path}: {e}")

    if txt_path.exists():
        for line in txt_path.read_text().splitlines():
            asin = line.strip().upper()
            if asin and len(asin) == 10 and asin.startswith("B"):
                asins[asin] = {
                    "asin": asin,
                    "title": "",
                    "market": "",
                    "source": "static_pool",
                }
        log.info(f"  static_pool (txt): {len(asins)} ASINs")

    return asins


def load_chrome_extension(path: Path) -> dict[str, dict]:
    """Load optional Chrome extension ASINs."""
    asins = {}
    if not path.exists():
        log.info("  chrome_extension: not found (optional)")
        return asins

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asin = row.get("asin", "").strip().upper()
            if asin and len(asin) == 10:
                asins[asin] = {
                    "asin": asin,
                    "title": row.get("title", ""),
                    "market": row.get("market", ""),
                    "source": "chrome_extension",
                }

    log.info(f"  chrome_extension: {len(asins)} ASINs")
    return asins


def merge_sources(data_dir: Path) -> dict[str, dict]:
    """Merge all sources, dedup by ASIN, track sources."""
    log.info("Phase 3a: Merging sources...")

    sources = {
        "keepa_search": load_keepa_search(data_dir / "keepa_search_qwertz.csv"),
        "amazon_scrape": load_amazon_scrape(data_dir / "amazon_asins_raw.csv"),
        "static_pool": load_static_pool(
            data_dir / "seed_asins_eu_qwertz.json",
            data_dir / "seed_asins_eu_qwertz.txt",
        ),
        "chrome_extension": load_chrome_extension(
            data_dir / "chrome_extension_asins.csv"
        ),
    }

    # Merge with source tracking
    merged = {}
    for source_name, source_data in sources.items():
        stats["sources"][source_name] = len(source_data)
        stats["total_raw"] += len(source_data)

        for asin, data in source_data.items():
            if asin in merged:
                # Keep richer data, append source
                existing = merged[asin]
                existing_sources = existing.get("all_sources", existing.get("source", ""))
                if source_name not in existing_sources:
                    existing["all_sources"] = f"{existing_sources},{source_name}"
                # Fill in missing fields
                if not existing.get("title") and data.get("title"):
                    existing["title"] = data["title"]
                if not existing.get("brand") and data.get("brand"):
                    existing["brand"] = data["brand"]
                if not existing.get("new_price") and data.get("new_price"):
                    existing["new_price"] = data["new_price"]
                if not existing.get("ean") and data.get("ean"):
                    existing["ean"] = data["ean"]
            else:
                data["all_sources"] = source_name
                merged[asin] = data

    stats["after_dedup"] = len(merged)
    log.info(f"  Merged: {stats['total_raw']} raw -> {stats['after_dedup']} unique ASINs")

    return merged


# ===== Phase 3b: Keepa /product Validation =====


async def validate_asins(
    client: KeepaClient, merged: dict[str, dict], target_domains: list[str]
) -> dict[str, dict]:
    """Batch-validate ASINs via Keepa /product for each target domain.
    Uses adaptive batch-steering based on client.rate_limit_remaining."""
    log.info("Phase 3b: Keepa /product batch validation...")

    all_asins = list(merged.keys())
    batch_size = 20  # Was 50 → conservative to avoid 429 avalanche

    for market in target_domains:
        domain_id = DOMAINS.get(market)
        if domain_id is None:
            continue

        log.info(f"  Validating {len(all_asins)} ASINs on {market} (domain={domain_id})...")

        for i in range(0, len(all_asins), batch_size):
            batch = all_asins[i : i + batch_size]

            # Token-budget pre-check: pause if tokens are low
            remaining = getattr(client, 'rate_limit_remaining', 100)
            if remaining < 20:
                reset_ms = getattr(client, 'rate_limit_reset', 15000)
                wait_s = min(max(reset_ms / 1000, 5), 60)
                log.info(f"    Pausing {wait_s:.0f}s — tokens low ({remaining})")
                await asyncio.sleep(wait_s)

            try:
                response = await client.get_products(batch, domain_id)
                stats["tokens_consumed"] += response.get("metadata", {}).get(
                    "tokens_consumed", 0
                )

                products = response.get("raw", {}).get("products", [])

                for product in products:
                    asin = product.get("asin", "")
                    if asin not in merged:
                        continue

                    entry = merged[asin]
                    title = product.get("title", "")
                    brand = product.get("brand", "")
                    csv_data = product.get("csv") or []
                    ean_list = product.get("eanList") or []
                    sales_rank = product.get("salesRankReference")
                    category_tree = product.get("categoryTree") or []
                    description = product.get("description", "") or ""
                    features_list = product.get("features") or []
                    features_text = " ".join(features_list) if isinstance(features_list, list) else str(features_list)

                    # Update with richer data
                    if description:
                        entry[f"description_{market}"] = description[:500]
                        if not entry.get("description"):
                            entry["description"] = description[:500]

                    if features_text.strip():
                        entry[f"features_{market}"] = features_text[:500]
                        if not entry.get("features"):
                            entry["features"] = features_text[:500]

                    if title:
                        entry[f"title_{market}"] = title[:200]
                        if not entry.get("title"):
                            entry["title"] = title[:200]

                    if brand and not entry.get("brand"):
                        entry["brand"] = brand

                    if ean_list and not entry.get("ean"):
                        entry["ean"] = ean_list[0]

                    if sales_rank:
                        entry[f"sales_rank_{market}"] = sales_rank

                    # Prices for this market
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

                    entry[f"new_price_{market}"] = new_price or amazon_price
                    entry[f"used_price_{market}"] = used_price

                    # Track cross-market presence
                    present_markets = entry.get("present_markets", set())
                    if isinstance(present_markets, str):
                        present_markets = set(present_markets.split(","))
                    if new_price or used_price or amazon_price:
                        present_markets.add(market)
                    entry["present_markets"] = present_markets

                    stats["validated"] += 1

            except Exception as e:
                log.warning(f"  Batch validation error on {market}: {e}")
                stats["errors"] += 1
                # On rate limit: extra pause instead of blindly continuing
                if "rate limit" in str(e).lower():
                    log.info("    Rate limited — extra 30s pause")
                    await asyncio.sleep(30)

            # Adaptive sleep based on token budget
            remaining = getattr(client, 'rate_limit_remaining', 100)
            if remaining > 50:
                await asyncio.sleep(2)
            elif remaining > 20:
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(15)

            progress = min(i + batch_size, len(all_asins))
            log.info(
                f"    {market}: {progress}/{len(all_asins)} | tokens_left={remaining}"
            )

        await asyncio.sleep(5)  # Was 3 → more breathing room between domains

    return merged


# ===== Phase 3c: Layout Detection =====


def detect_layout_text(title: str) -> tuple[str | None, str]:
    """Layer 1: Detect layout from title keywords."""
    title_lower = title.lower()
    for layout, keywords in LAYOUT_TITLE_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return layout, "title_keyword"
    return None, ""


def detect_layout_brand_model(title: str, brand: str) -> tuple[str | None, str]:
    """Layer 2: Detect layout from known QWERTZ brand+model combos."""
    combined = f"{brand} {title}".lower()
    for model in KNOWN_QWERTZ_MODELS:
        if model in combined:
            return "qwertz", "brand_model_db"
    return None, ""


def detect_layout_ean(ean: str) -> tuple[str | None, str]:
    """Layer 3: EAN prefix 400-440 = German origin."""
    if not ean or len(ean) < 3:
        return None, ""
    prefix = ean[:3]
    try:
        prefix_int = int(prefix)
        if 400 <= prefix_int <= 440:
            return "qwertz", "ean_prefix"
    except ValueError:
        pass
    return None, ""


def detect_layout_cross_market(present_markets: set) -> tuple[str | None, str]:
    """Layer 4: Cross-market presence (DE + non-DE market)."""
    if "DE" in present_markets and len(present_markets) > 1:
        non_de = present_markets - {"DE"}
        if non_de:
            return "qwertz", "cross_market"
    return None, ""


def detect_layout(entry: dict) -> dict:
    """Run multi-layer layout detection on a single entry."""
    title = entry.get("title", "")
    brand = entry.get("brand", "")
    ean = entry.get("ean", "")
    present_markets = entry.get("present_markets", set())
    if isinstance(present_markets, str):
        present_markets = set(present_markets.split(",")) if present_markets else set()

    # Also check market-specific titles
    all_titles = [title]
    for market in DOMAINS:
        mt = entry.get(f"title_{market}", "")
        if mt and mt not in all_titles:
            all_titles.append(mt)

    combined_title = " ".join(all_titles)

    # Combine description + features from all markets
    description = entry.get("description", "")
    features = entry.get("features", "")
    for market in DOMAINS:
        desc_m = entry.get(f"description_{market}", "")
        feat_m = entry.get(f"features_{market}", "")
        if desc_m and desc_m not in description:
            description += " " + desc_m
        if feat_m and feat_m not in features:
            features += " " + feat_m

    # Layer 1 checks title + description + features
    combined_text = combined_title + " " + description + " " + features

    # Layer 1: Title/description/features keywords (high confidence)
    layout, layer = detect_layout_text(combined_text)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "high"}

    # Layer 2: Brand+Model DB (high confidence)
    layout, layer = detect_layout_brand_model(combined_title, brand)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "high"}

    # Layer 3: EAN prefix (medium confidence)
    layout, layer = detect_layout_ean(ean)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "medium"}

    # Layer 4: Cross-market presence (low confidence)
    layout, layer = detect_layout_cross_market(present_markets)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "low"}

    return {"detected_layout": "unknown", "detection_layer": "none", "confidence": "none"}


def classify_mismatch(
    detected_layout: str, market: str
) -> tuple[bool, str]:
    """Determine if detected layout mismatches expected layout for market."""
    expected = EXPECTED_LAYOUT.get(market, "")
    if detected_layout == "unknown":
        return False, "unknown"
    if not expected:
        return False, "unknown"
    if detected_layout != expected:
        return True, detected_layout
    return False, detected_layout


def run_layout_detection(
    merged: dict[str, dict], target_domains: list[str]
) -> list[dict]:
    """Phase 3c: Run layout detection and classify mismatches."""
    log.info("Phase 3c: Multi-layer layout detection...")

    results = []

    for asin, entry in merged.items():
        detection = detect_layout(entry)
        detected_layout = detection["detected_layout"]
        detection_layer = detection["detection_layer"]
        confidence = detection["confidence"]

        # For each target market, check mismatch
        present_markets = entry.get("present_markets", set())
        if isinstance(present_markets, str):
            present_markets = set(present_markets.split(",")) if present_markets else set()

        # If we have market-specific data, use it
        entry_market = entry.get("market", "")
        if entry_market:
            markets_to_check = [entry_market] if entry_market in target_domains else []
        else:
            markets_to_check = [m for m in target_domains if m in present_markets]

        # If no specific market, check all target domains
        if not markets_to_check:
            markets_to_check = target_domains

        for market in markets_to_check:
            domain_id = DOMAINS.get(market, 0)
            expected = EXPECTED_LAYOUT.get(market, "")
            is_mismatch, _ = classify_mismatch(detected_layout, market)

            # Confidence classification
            if is_mismatch and confidence in ("high",):
                mismatch_type = "confirmed_mismatch"
                stats["confirmed_mismatch"] += 1
            elif is_mismatch and confidence in ("medium", "low"):
                mismatch_type = "suspected_mismatch"
                stats["suspected_mismatch"] += 1
            else:
                mismatch_type = "unknown"
                stats["unknown"] += 1

            new_price = entry.get(f"new_price_{market}") or entry.get("new_price")
            used_price = entry.get(f"used_price_{market}") or entry.get("used_price")

            results.append(
                {
                    "asin": asin,
                    "domain_id": domain_id,
                    "market": market,
                    "title": entry.get(f"title_{market}") or entry.get("title", ""),
                    "detected_layout": detected_layout,
                    "expected_layout": expected,
                    "is_mismatch": is_mismatch,
                    "confidence": confidence,
                    "detection_layer": detection_layer,
                    "new_price": new_price,
                    "used_price": used_price,
                    "brand": entry.get("brand", ""),
                    "source": entry.get("all_sources", entry.get("source", "")),
                }
            )

    log.info(f"  Layout detection complete: {len(results)} entries")
    log.info(
        f"  Confirmed: {stats['confirmed_mismatch']}, "
        f"Suspected: {stats['suspected_mismatch']}, "
        f"Unknown: {stats['unknown']}"
    )

    return results


# ===== Phase 3d: Output =====


def write_output(results: list[dict], output_path: Path):
    """Write final results to CSV and JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "asin",
        "domain_id",
        "market",
        "title",
        "detected_layout",
        "expected_layout",
        "is_mismatch",
        "confidence",
        "detection_layer",
        "new_price",
        "used_price",
        "brand",
        "source",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    log.info(f"Saved {len(results)} records to {output_path}")

    # JSON with metadata
    json_path = output_path.with_suffix(".json")
    by_confidence = defaultdict(list)
    by_market = defaultdict(list)
    mismatches = [r for r in results if r["is_mismatch"]]

    for r in results:
        by_confidence[r["confidence"]].append(r["asin"])
        by_market[r["market"]].append(r["asin"])

    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(results),
            "unique_asins": len(set(r["asin"] for r in results)),
            "mismatches": len(mismatches),
            "stats": {k: v for k, v in stats.items() if k != "sources"},
            "sources": stats["sources"],
            "confidence_distribution": {
                k: len(v) for k, v in by_confidence.items()
            },
            "by_market": {k: len(v) for k, v in by_market.items()},
        },
        "mismatches": mismatches,
        "all_entries": results,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    log.info(f"Saved JSON to {json_path}")


# ===== Helpers =====


def _parse_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ===== Main =====


async def check_token_budget(client: KeepaClient) -> int:
    """Check available Keepa API tokens before starting validation."""
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"https://api.keepa.com/token?key={client.api_key}"
            )
            data = resp.json()
            tokens = data.get("tokensLeft", 0)
            refill = data.get("refillRate", 0)
            refill_in = data.get("refillIn", 0)
            log.info(f"  Token budget: {tokens} left, refill {refill}/min, next in {refill_in}ms")

            if tokens < 50:
                wait_s = min(refill_in / 1000, 120) if refill_in > 0 else 60
                log.warning(f"  Low tokens ({tokens}) — waiting {wait_s:.0f}s for refill")
                await asyncio.sleep(wait_s)

            return tokens
    except Exception as e:
        log.warning(f"  Token check failed: {e} — proceeding anyway")
        return -1


async def run(args):
    data_dir = PROJECT_ROOT / "data"

    # Phase 3a: Merge
    merged = merge_sources(data_dir)

    if not merged:
        log.error("No ASINs found from any source!")
        return

    # Phase 3b: Validate (unless skipped)
    target_domains = [m.strip().upper() for m in args.domains.split(",")]

    if not args.skip_validation:
        api_key = get_api_key()
        if not api_key:
            log.error("KEEPA_API_KEY not set. Use --skip-validation or set key.")
            sys.exit(1)

        client = KeepaClient(api_key=api_key)

        # Check token budget before starting
        log.info("  Checking Keepa token budget...")
        await check_token_budget(client)

        # Always include DE for baseline + target domains
        validation_domains = list(dict.fromkeys(["DE"] + target_domains))
        merged = await validate_asins(client, merged, validation_domains)
    else:
        log.info("Phase 3b: SKIPPED (--skip-validation)")

    # Phase 3c: Layout Detection
    results = run_layout_detection(merged, target_domains)

    # Phase 3d: Output
    output_path = PROJECT_ROOT / args.output
    write_output(results, output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Collect 1000+ keyboard layout-mismatch targets"
    )
    parser.add_argument(
        "--output",
        default="data/keyboard_targets_1000.csv",
        help="Output CSV path (default: data/keyboard_targets_1000.csv)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip Keepa /product validation (just merge + detect layout)",
    )
    parser.add_argument(
        "--domains",
        default="UK,FR,IT,ES",
        help="Target domains for mismatch check (default: UK,FR,IT,ES)",
    )
    args = parser.parse_args()

    log.info("Keyboard Collection Orchestrator")
    log.info(f"  Target domains: {args.domains}")
    log.info(f"  Validation: {'SKIP' if args.skip_validation else 'ON'}")
    log.info(f"  Output: {args.output}")

    start = time.time()
    asyncio.run(run(args))
    elapsed = time.time() - start

    log.info(f"\nComplete in {elapsed / 60:.1f} min")
    log.info(f"Stats: {json.dumps(stats, indent=2, default=str)}")


if __name__ == "__main__":
    main()
