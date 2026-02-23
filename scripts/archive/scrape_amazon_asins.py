#!/usr/bin/env python3
"""
Amazon Keyboard ASIN Scraper v3 — Layout-Mismatch Discovery
============================================================
Scrapes Amazon search results across 5 EU markets to find keyboard ASINs.
DE as baseline (find German keyboards), then cross-market (UK/FR/IT/ES)
to find layout mismatches (e.g. QWERTZ on Amazon.fr).

Features:
- 15+ keywords per market (market-specific language)
- 3 pages pagination per keyword
- playwright-stealth for anti-detection
- User-Agent rotation (5+ current Chrome UAs)
- Random delays (5-12s between requests)
- Session rotation (new browser context every 20 requests)
- Viewport variation
- Rate limiting: max 50 requests/hour per domain
- Captcha handling: 5min pause + new context

Output: data/amazon_asins_raw.csv + data/amazon_asins_raw.json
"""

import asyncio
import csv
import json
import random
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth

    _stealth_instance = Stealth()
    STEALTH_AVAILABLE = True

    async def stealth_async(page):
        """Adapter for playwright-stealth v2 API."""
        await _stealth_instance.apply_stealth_async(page)
except ImportError:
    STEALTH_AVAILABLE = False
    print("WARNING: playwright-stealth not installed. Running without stealth.")
    print("Install with: pip install playwright-stealth")


# ---------------------------------------------------------------------------
# Keywords: 15+ per market, market-specific language
# ---------------------------------------------------------------------------
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
        "ducky tastatur",
        "ergonomische tastatur",
        "mini tastatur",
        "60% tastatur",
        "tkl tastatur",
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

# Amazon domain mapping
DOMAIN_MAP = {
    "DE": "amazon.de",
    "UK": "amazon.co.uk",
    "FR": "amazon.fr",
    "IT": "amazon.it",
    "ES": "amazon.es",
}

# Keyboard category node IDs per market (for category filtering in search URL)
CATEGORY_NODES = {
    "DE": "340843031",
    "UK": "340831031",
    "FR": "430332031",
    "IT": "460161031",
    "ES": "937912031",
}

# User-Agent rotation pool (current Chrome on Windows/Mac)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]

# Viewport sizes for randomization
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
]

# Max pages to scrape per keyword
MAX_PAGES = 3

# Rate limiting
MAX_REQUESTS_PER_HOUR = 50
REQUEST_DELAY_MIN = 5.0
REQUEST_DELAY_MAX = 12.0
CONTEXT_ROTATION_INTERVAL = 20  # New browser context every N requests
CAPTCHA_PAUSE_SECONDS = 300  # 5 minutes


class RateLimiter:
    """Per-domain rate limiter: max N requests per hour."""

    def __init__(self, max_per_hour: int = MAX_REQUESTS_PER_HOUR):
        self.max_per_hour = max_per_hour
        self.timestamps: dict[str, list[float]] = defaultdict(list)

    def can_request(self, domain: str) -> bool:
        now = time.time()
        cutoff = now - 3600
        self.timestamps[domain] = [t for t in self.timestamps[domain] if t > cutoff]
        return len(self.timestamps[domain]) < self.max_per_hour

    def record(self, domain: str):
        self.timestamps[domain].append(time.time())

    def wait_time(self, domain: str) -> float:
        if self.can_request(domain):
            return 0
        oldest = min(self.timestamps[domain])
        return max(0, oldest + 3600 - time.time())


class Stats:
    """Track scraping stats."""

    def __init__(self):
        self.total_requests = 0
        self.total_asins = 0
        self.captchas = 0
        self.errors = 0
        self.by_market: dict[str, int] = defaultdict(int)
        self.start_time = time.time()


async def create_browser_context(playwright, stealth_enabled: bool):
    """Create a new browser context with randomized fingerprint."""
    ua = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)

    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale=random.choice(["de-DE", "en-GB", "fr-FR", "it-IT", "es-ES"]),
        timezone_id=random.choice(
            [
                "Europe/Berlin",
                "Europe/London",
                "Europe/Paris",
                "Europe/Rome",
                "Europe/Madrid",
            ]
        ),
    )

    page = await context.new_page()

    # Apply stealth if available
    if stealth_enabled and STEALTH_AVAILABLE:
        await stealth_async(page)
    else:
        # Basic anti-detection without stealth plugin
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['de-DE', 'de', 'en-US', 'en']});
            window.chrome = {runtime: {}};
        """
        )

    return browser, context, page


async def scrape_search_page(
    page, market: str, keyword: str, page_num: int, max_results: int = 25
) -> list[dict]:
    """Scrape a single Amazon search results page."""
    results = []

    domain = DOMAIN_MAP[market]
    search = keyword.replace(" ", "+")
    cat_node = CATEGORY_NODES.get(market, "")

    # Build URL with category filter
    url = f"https://www.{domain}/s?k={search}&page={page_num}"
    if cat_node:
        url += f"&rh=n:{cat_node}"

    try:
        response = await page.goto(url, timeout=30000, wait_until="domcontentloaded")

        if response and response.status >= 400:
            print(f"    HTTP {response.status} for {market}/{keyword} p{page_num}")
            return results

        await asyncio.sleep(random.uniform(1.0, 2.5))

        # Check for captcha
        current_url = page.url.lower()
        page_content = await page.content()
        if "captcha" in current_url or "captcha" in page_content.lower()[:2000]:
            print(f"    CAPTCHA detected on {market}/{keyword} p{page_num}!")
            return []  # Signal captcha to caller

        # Wait for product results
        try:
            await page.wait_for_selector(
                ".s-result-item[data-asin]", timeout=8000
            )
        except Exception:
            print(f"    No products found: {market}/{keyword} p{page_num}")
            return results

        # Extract ASINs and product info
        products = await page.query_selector_all(".s-result-item[data-asin]")

        for product in products[:max_results]:
            try:
                asin = await product.get_attribute("data-asin")
                if not asin or len(asin) != 10 or not asin.startswith("B"):
                    continue

                # Title
                title_elem = await product.query_selector(
                    "h2 a span, .a-color-base.a-text-normal"
                )
                title = ""
                if title_elem:
                    title = (await title_elem.inner_text()).strip()[:150]

                if not title:
                    continue

                # Price
                price = None
                price_elem = await product.query_selector(".a-price .a-offscreen")
                if not price_elem:
                    price_elem = await product.query_selector(".a-price-whole")
                if price_elem:
                    try:
                        price_text = await price_elem.inner_text()
                        price_text = (
                            price_text.replace("€", "")
                            .replace("£", "")
                            .replace("\xa0", "")
                            .replace(",", ".")
                            .strip()
                        )
                        # Handle "29.99" or "29" format
                        price = float(price_text)
                    except (ValueError, TypeError):
                        pass

                # Rating
                rating = None
                rating_elem = await product.query_selector(
                    ".a-icon-star-small .a-icon-alt, .a-icon-star .a-icon-alt"
                )
                if rating_elem:
                    try:
                        rating_text = await rating_elem.inner_text()
                        # "4,3 von 5 Sternen" or "4.3 out of 5 stars"
                        rating_num = (
                            rating_text.split(" ")[0].replace(",", ".")
                        )
                        rating = float(rating_num)
                    except (ValueError, TypeError, IndexError):
                        pass

                results.append(
                    {
                        "asin": asin.upper(),
                        "title": title,
                        "price": price,
                        "rating": rating,
                        "market": market,
                        "keyword": keyword,
                        "page": page_num,
                        "source": "amazon_scrape",
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

            except Exception:
                continue

    except Exception as e:
        print(f"    Error scraping {market}/{keyword} p{page_num}: {e}")

    return results


async def scrape_all(markets: list[str], dry_run: bool = False):
    """Main scraping loop across all markets and keywords."""
    stats = Stats()
    rate_limiter = RateLimiter(MAX_REQUESTS_PER_HOUR)
    all_results = []
    seen_asins: dict[str, set] = defaultdict(set)  # per-market dedup
    request_count = 0

    async with async_playwright() as p:
        browser, context, page = await create_browser_context(
            p, stealth_enabled=True
        )

        for market in markets:
            keywords = SEARCH_KEYWORDS.get(market, [])
            domain = DOMAIN_MAP[market]
            print(f"\n{'='*60}")
            print(f"SCRAPING {market} ({domain}) — {len(keywords)} keywords")
            print(f"{'='*60}")

            for keyword in keywords:
                for page_num in range(1, MAX_PAGES + 1):
                    # Rate limiting check
                    wait = rate_limiter.wait_time(domain)
                    if wait > 0:
                        print(
                            f"  Rate limit reached for {domain}. "
                            f"Waiting {wait:.0f}s..."
                        )
                        await asyncio.sleep(wait)

                    # Context rotation
                    request_count += 1
                    if request_count % CONTEXT_ROTATION_INTERVAL == 0:
                        print("  Rotating browser context...")
                        await browser.close()
                        browser, context, page = await create_browser_context(
                            p, stealth_enabled=True
                        )

                    # Random delay between requests
                    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                    await asyncio.sleep(delay)

                    print(
                        f"  [{market}] {keyword} (p{page_num}) "
                        f"[req #{stats.total_requests + 1}]..."
                    )

                    if dry_run:
                        print(f"    DRY RUN — would scrape {domain}")
                        continue

                    results = await scrape_search_page(
                        page, market, keyword, page_num
                    )
                    stats.total_requests += 1
                    rate_limiter.record(domain)

                    # Handle captcha (empty results = captcha signal)
                    if not results and page_num == 1:
                        # Could be captcha or no results
                        content = await page.content()
                        if "captcha" in content.lower()[:2000]:
                            stats.captchas += 1
                            print(
                                f"    CAPTCHA! Pausing {CAPTCHA_PAUSE_SECONDS}s "
                                f"and rotating context..."
                            )
                            await browser.close()
                            await asyncio.sleep(CAPTCHA_PAUSE_SECONDS)
                            browser, context, page = await create_browser_context(
                                p, stealth_enabled=True
                            )
                            break  # Skip remaining pages for this keyword

                    # Dedup within market
                    new_count = 0
                    for r in results:
                        if r["asin"] not in seen_asins[market]:
                            seen_asins[market].add(r["asin"])
                            all_results.append(r)
                            new_count += 1

                    stats.by_market[market] += new_count
                    stats.total_asins += new_count
                    print(f"    +{new_count} new ASINs (total: {stats.total_asins})")

                    # If no results on this page, skip remaining pages
                    if len(results) == 0:
                        break

            print(
                f"\n  {market} subtotal: {stats.by_market[market]} unique ASINs"
            )

        await browser.close()

    elapsed = time.time() - stats.start_time
    print(f"\n{'='*60}")
    print(f"SCRAPING COMPLETE")
    print(f"  Total ASINs: {stats.total_asins}")
    print(f"  Requests: {stats.total_requests}")
    print(f"  Captchas: {stats.captchas}")
    print(f"  Duration: {elapsed/60:.1f} minutes")
    print(f"  By market: {dict(stats.by_market)}")
    print(f"{'='*60}")

    return all_results, stats


def save_results(results: list[dict], output_path: Path):
    """Save results to CSV and JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # CSV
    fieldnames = [
        "asin",
        "title",
        "price",
        "rating",
        "market",
        "keyword",
        "page",
        "source",
        "scraped_at",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Saved {len(results)} ASINs to {output_path}")

    # JSON (with metadata)
    json_path = output_path.with_suffix(".json")
    markets_found = sorted(set(r["market"] for r in results))
    by_market = defaultdict(list)
    for r in results:
        by_market[r["market"]].append(r["asin"])

    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(results),
            "markets": markets_found,
            "by_market_count": {m: len(asins) for m, asins in by_market.items()},
        },
        "asins": list(dict.fromkeys(r["asin"] for r in results)),
        "by_market": {m: list(dict.fromkeys(asins)) for m, asins in by_market.items()},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Saved JSON to {json_path}")


def _set_max_pages(value: int):
    global MAX_PAGES
    MAX_PAGES = value


async def main():
    parser = argparse.ArgumentParser(
        description="Amazon Keyboard ASIN Scraper v3"
    )
    parser.add_argument(
        "--market",
        "-m",
        default="all",
        help="Market(s): DE,UK,FR,IT,ES or 'all' (default: all)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="data/amazon_asins_raw.csv",
        help="Output CSV path (default: data/amazon_asins_raw.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be scraped without actually scraping",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=MAX_PAGES,
        help=f"Pages per keyword (default: {MAX_PAGES})",
    )
    args = parser.parse_args()

    _set_max_pages(args.pages)

    if args.market.lower() == "all":
        markets = list(SEARCH_KEYWORDS.keys())
    else:
        markets = [m.strip().upper() for m in args.market.split(",")]
        invalid = [m for m in markets if m not in SEARCH_KEYWORDS]
        if invalid:
            print(f"ERROR: Unknown markets: {invalid}")
            print(f"Valid: {list(SEARCH_KEYWORDS.keys())}")
            return

    total_requests = sum(len(SEARCH_KEYWORDS[m]) for m in markets) * MAX_PAGES
    est_hours = total_requests * ((REQUEST_DELAY_MIN + REQUEST_DELAY_MAX) / 2) / 3600

    print(f"Amazon Keyboard ASIN Scraper v3")
    print(f"  Markets: {markets}")
    print(f"  Keywords: {sum(len(SEARCH_KEYWORDS[m]) for m in markets)}")
    print(f"  Pages/keyword: {MAX_PAGES}")
    print(f"  Est. requests: {total_requests}")
    print(f"  Est. duration: {est_hours:.1f} hours")
    print(f"  Stealth: {'YES' if STEALTH_AVAILABLE else 'NO (install playwright-stealth)'}")

    results, stats = await scrape_all(markets, dry_run=args.dry_run)

    if results:
        output = Path(__file__).parent.parent / args.output
        save_results(results, output)


if __name__ == "__main__":
    asyncio.run(main())
