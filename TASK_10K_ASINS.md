# TASK: Scale to 10,000+ ASINs via Continuous Discovery

> **Lies diese Datei komplett, bevor du irgendetwas anfasst.**
> Ziel: Eine neue Claude Code Session kann hiermit OHNE weitere Fragen loslegen.

---

## 1. Kontext

Dieses Projekt ist ein **Keepa-basiertes Keyboard-Layout-Arbitrage-System** fuer Samuels Data-Engineering-Abgabe (HS Worms). Es erkennt QWERTZ-Tastaturen, die auf nicht-deutschen Amazon-Maerkten (UK, FR, IT, ES) falsch gelistet sind — also Layout-Mismatches.

### Aktueller Stand (2026-02-24)

| Metrik | Wert |
|--------|------|
| Unique ASINs in CSV | **1.205** |
| Davon `is_mismatch=True` | **117 (9.7%)** |
| Layout-Erkennung "unknown" | 493 (40.9%) |
| Maerkte validiert | Nur **DE** (kein Cross-Market!) |
| Seed-Datei (`seed_asins_eu_qwertz.txt`) | **LEER (0 Bytes)** |
| Token-Budget (Keepa Starter Plan) | ~1.200 Tokens/Stunde |

### Was fehlt

1. **Mehr ASINs** — Von 1.205 auf 10.000+ skalieren
2. **Continuous Discovery** — Automatisiert neue ASINs finden, nicht nur einmal manuell
3. **Cross-Market-Validierung** — Aktuell nur DE, aber UK/FR/IT/ES brauchen wir fuer echte Mismatches

---

## 2. Ziel

```
1.205 ASINs (manuell)  →  10.000+ ASINs (automatisch, continuous)
Nur DE validiert        →  5 Maerkte (DE, UK, FR, IT, ES)
117 Mismatches          →  1.000+ Mismatches (durch mehr ASINs + Cross-Market)
```

Die Discovery soll als **Teil des bestehenden Schedulers** laufen — kein separates Script, kein Cronjob.

---

## 3. Bestandsaufnahme — Wichtige Dateien

### Kern-Code

| Datei | LOC | Funktion |
|-------|-----|----------|
| `src/scheduler.py` | 866 | Scheduler-Loop mit `run_scheduler()`, `run_price_check()`, `collect_deals_to_elasticsearch()` |
| `src/services/keepa_client.py` | 878 | Light HTTP Client — `search_products()`, `get_bestsellers()`, `product_finder()`, `get_products()`, `get_deals()` |
| `src/services/keepa_api.py` | ~700 | Heavy Client (nutzt `keepa` Library) — `query_product()`, Token Bucket |
| `src/config.py` | 72 | `Settings(BaseSettings)` mit pydantic — `get_settings()` |
| `src/agents/deal_finder.py` | ~578 | Deal-Scoring, `search_deals()`, `_build_deal_from_product()` |

### Discovery-Scripts (existieren, aber als Standalone)

| Datei | Was es tut |
|-------|-----------|
| `scripts/collect_1000_keyboards.py` | Orchestrator: Merge Sources → Keepa Validation → Layout Detection → CSV Output |
| `scripts/discover_bestseller_keyboards.py` | 3-Step Discovery: Category Search → Bestsellers → Product Finder |

### Daten

| Datei | Inhalt |
|-------|--------|
| `data/keyboard_targets_1000.csv` | **1.205 Zeilen** — ASIN, market, title, detected_layout, is_mismatch, confidence, etc. |
| `data/keyboard_targets_1000.json` | JSON-Version mit Metadaten |
| `data/keepa_search_qwertz.csv` | 195 ASINs aus Keepa `/search` |
| `data/amazon_asins_raw.csv` | 1.135 ASINs aus Amazon Scraper |
| `data/seed_asins_eu_qwertz.txt` | **LEER** — muss befuellt werden |
| `data/seed_asins_eu_qwertz.json` | Nur Stats, 0 ASINs |

### Layout-Detection (4-Layer-Heuristik)

Die Logik liegt komplett in `scripts/collect_1000_keyboards.py`, Zeilen 63-599:

**Layer 1 — Title Keywords** (confidence: high)
```python
LAYOUT_TITLE_KEYWORDS = {
    "qwertz": ["qwertz", "deutsch", "german layout", "iso-de", ...],
    "azerty": ["azerty", "french layout", "clavier francais", ...],
    "qwerty_uk": ["uk layout", "british layout", ...],
    "qwerty_it": ["italian layout", "italiano", ...],
    "qwerty_es": ["spanish layout", "espanol", ...],
}
```

**Layer 2 — Brand+Model DB** (confidence: high)
```python
KNOWN_QWERTZ_MODELS = [
    "cherry kc 1000", "cherry stream", "logitech k120 de",
    "corsair k70 de", "razer blackwidow de", ...  # ~52 Eintraege
]
```

**Layer 3 — EAN Prefix** (confidence: medium)
```python
# EAN 400-440 = Deutschland
if 400 <= int(ean[:3]) <= 440: return "qwertz"
```

**Layer 4 — Cross-Market** (confidence: low)
```python
# Wenn ASIN auf DE + anderem Markt existiert
if "DE" in present_markets and len(present_markets) > 1: return "qwertz"
```

### Expected Layout per Market

```python
EXPECTED_LAYOUT = {
    "DE": "qwertz",
    "UK": "qwerty_uk",
    "FR": "azerty",
    "IT": "qwerty_it",
    "ES": "qwerty_es",
}
```

### Keepa Client API-Methoden (keepa_client.py)

| Methode | Tokens | Was |
|---------|--------|-----|
| `search_products(term, domain_id, category, page)` | ~5-10/call | Keyword-Suche |
| `get_bestsellers(domain_id, category)` | ~1/call | Top-ASINs einer Kategorie |
| `product_finder(domain_id, product_parms)` | ~5-15/call | Filter-basierte Suche |
| `get_products(asins, domain_id)` | ~1/ASIN | Produkt-Details + Preise |
| `get_deals(domain_id, ...)` | ~50/call | Deal-Endpunkt (braucht Starter Plan) |

### Kategorie-IDs (Amazon Browse Nodes)

```python
FALLBACK_KEYBOARD_CATEGORIES = {
    3: [340843031],    # DE: Computer-Tastaturen
    2: [340831031],    # UK: Computer Keyboards
    4: [340843031],    # FR
    8: [340843031],    # IT
    9: [340843031],    # ES
}
```

---

## 4. Aufgabe 1: Continuous Discovery im Scheduler

### Was

Neue async Methode `run_asin_discovery()` in `src/scheduler.py` (Klasse `PriceMonitorScheduler`), die bei jedem Scheduler-Cycle automatisch neue ASINs findet.

### Wie

```python
async def run_asin_discovery(self):
    """
    Continuous ASIN discovery — laeuft als Background-Task im Scheduler.
    Pro Cycle: 1 Discovery-Strategie ausfuehren, neue ASINs zur CSV appenden.
    Rotiert durch 3 Strategien:
      1. search_products() mit rotierenden Suchbegriffen
      2. get_bestsellers() ueber alle 5 Maerkte
      3. product_finder() mit SalesRank-Filter
    """
```

### Integration in run_scheduler()

In `run_scheduler()` (Zeile 258 in scheduler.py), NACH dem bestehenden `collect_deals_to_elasticsearch()` Task:

```python
# Phase 4: Launch background deal collector after all services ready
asyncio.create_task(self.collect_deals_to_elasticsearch())
# NEU: Phase 5: Continuous ASIN Discovery
asyncio.create_task(self.run_asin_discovery())
```

### Discovery-Loop Logik

```python
async def run_asin_discovery(self):
    from scripts.collect_1000_keyboards import (
        detect_layout, classify_mismatch, EXPECTED_LAYOUT, DOMAINS
    )

    # Lade existierende ASINs fuer Dedup
    existing_asins = self._load_existing_asins_from_csv()

    # Light Client fuer guenstige API-Calls
    from src.services.keepa_client import KeepaClient
    light_client = KeepaClient()

    strategy_cycle = 0
    search_term_index = 0

    while self.running:
        strategy = strategy_cycle % 3

        try:
            new_asins = []

            if strategy == 0:
                # Strategie 1: search_products() mit rotierenden Suchbegriffen
                terms = self._get_discovery_search_terms()
                term = terms[search_term_index % len(terms)]
                search_term_index += 1
                # Rotiere durch Maerkte
                market_keys = list(DOMAINS.keys())
                market = market_keys[strategy_cycle // 3 % len(market_keys)]
                domain_id = DOMAINS[market]

                result = await light_client.search_products(term, domain_id)
                products = result.get("raw", {}).get("products", [])
                for p in products:
                    asin = p.get("asin", "").upper()
                    if asin and len(asin) == 10 and asin not in existing_asins:
                        new_asins.append({
                            "asin": asin,
                            "title": p.get("title", ""),
                            "market": market,
                            "domain_id": domain_id,
                            "source": "discovery_search",
                        })

            elif strategy == 1:
                # Strategie 2: get_bestsellers() je Markt
                for market, domain_id in DOMAINS.items():
                    categories = FALLBACK_KEYBOARD_CATEGORIES.get(domain_id, [340843031])
                    for cat_id in categories:
                        result = await light_client.get_bestsellers(domain_id, cat_id)
                        asin_list = result.get("raw", {}).get("bestSellersList") or []
                        for asin in asin_list:
                            asin = asin.upper() if isinstance(asin, str) else ""
                            if asin and len(asin) == 10 and asin not in existing_asins:
                                new_asins.append({
                                    "asin": asin,
                                    "market": market,
                                    "domain_id": domain_id,
                                    "source": "discovery_bestseller",
                                })
                        await asyncio.sleep(2)  # Rate-limit pause

            elif strategy == 2:
                # Strategie 3: product_finder() mit SalesRank-Filter
                for market, domain_id in DOMAINS.items():
                    cat_ids = FALLBACK_KEYBOARD_CATEGORIES.get(domain_id, [340843031])
                    parms = {
                        "productType": [0],
                        "rootCategory": cat_ids[0],
                        "salesRankRange": [1, 100000],
                        "hasReviews": True,
                    }
                    result = await light_client.product_finder(domain_id, parms)
                    asin_list = result.get("raw", {}).get("asinList") or []
                    for asin in asin_list:
                        asin = asin.upper() if isinstance(asin, str) else ""
                        if asin and len(asin) == 10 and asin not in existing_asins:
                            new_asins.append({
                                "asin": asin,
                                "market": market,
                                "domain_id": domain_id,
                                "source": "discovery_finder",
                            })
                    await asyncio.sleep(3)

            # Inline Layout-Detection + Append zur CSV
            if new_asins:
                self._append_discovered_asins(new_asins, existing_asins)
                logger.info(
                    f"Discovery Cycle #{strategy_cycle}: "
                    f"{len(new_asins)} new ASINs (Strategy {strategy})"
                )

        except Exception as e:
            logger.warning(f"Discovery error: {e}")

        strategy_cycle += 1

        # Discovery laeuft seltener als Price-Check — alle 30 Minuten
        await asyncio.sleep(self.settings.discovery_interval_seconds)
```

### Hilfsmethoden (ebenfalls in PriceMonitorScheduler)

```python
def _load_existing_asins_from_csv(self) -> set:
    """Lade alle bekannten ASINs aus keyboard_targets_1000.csv."""
    from pathlib import Path
    import csv
    existing = set()
    csv_path = Path(__file__).parent.parent / "data" / "keyboard_targets_1000.csv"
    if csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing.add(row.get("asin", "").upper())
    return existing

def _append_discovered_asins(self, new_asins: list, existing: set):
    """Append neue ASINs zur CSV mit Inline-Layout-Detection."""
    from pathlib import Path
    from scripts.collect_1000_keyboards import (
        detect_layout, classify_mismatch, EXPECTED_LAYOUT, DOMAINS
    )
    import csv

    csv_path = Path(__file__).parent.parent / "data" / "keyboard_targets_1000.csv"
    fieldnames = [
        "asin", "domain_id", "market", "title", "detected_layout",
        "expected_layout", "is_mismatch", "confidence", "detection_layer",
        "new_price", "used_price", "brand", "source",
    ]

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for entry in new_asins:
            asin = entry["asin"]
            if asin in existing:
                continue

            # Layout Detection
            detection = detect_layout(entry)
            market = entry.get("market", "DE")
            expected = EXPECTED_LAYOUT.get(market, "")
            detected = detection["detected_layout"]
            is_mismatch = (detected != "unknown" and detected != expected) if expected else False

            writer.writerow({
                "asin": asin,
                "domain_id": entry.get("domain_id", DOMAINS.get(market, 3)),
                "market": market,
                "title": entry.get("title", "")[:200],
                "detected_layout": detected,
                "expected_layout": expected,
                "is_mismatch": is_mismatch,
                "confidence": detection["confidence"],
                "detection_layer": detection["detection_layer"],
                "new_price": entry.get("new_price"),
                "used_price": entry.get("used_price"),
                "brand": entry.get("brand", ""),
                "source": entry.get("source", "discovery"),
            })
            existing.add(asin)
```

---

## 5. Aufgabe 2: Token-Budget Aufteilung

Keepa Starter Plan = **~1.200 Tokens/Stunde** (20 Tokens/Minute Refill).

| Task | Tokens/h | Anteil | Begründung |
|------|----------|--------|------------|
| Price Rotation (`run_price_check`) | 800 | 66.7% | Kerngeschaeft — Preisueberwachung |
| Discovery (`run_asin_discovery`) | 200 | 16.7% | Reserve, opportunistisch |
| Deal Collector (`collect_deals_to_elasticsearch`) | 100 | 8.3% | Bestehend |
| Reserve/Safety | 100 | 8.3% | Buffer fuer Retries/Bursts |
| **Gesamt** | **1.200** | **100%** | |

### Implementierung

Discovery-Loop soll **vor jedem API-Call** pruefen:
```python
remaining = getattr(light_client, 'rate_limit_remaining', 100)
if remaining < 50:  # Mindestens 50 Tokens Reserve lassen
    logger.info("Discovery pausing — tokens low (%d), reserving for price checks", remaining)
    await asyncio.sleep(300)  # 5 Min warten, Tokens fuer Price-Check freihalten
    continue
```

---

## 6. Aufgabe 3: Discovery-Suchbegriffe rotieren

Neue Methode in `PriceMonitorScheduler`:

```python
DISCOVERY_SEARCH_TERMS = {
    "DE": [
        "tastatur", "mechanische tastatur", "gaming keyboard",
        "bluetooth tastatur", "kabellose tastatur", "usb tastatur",
        "mini tastatur", "ergonomische tastatur",
    ],
    "UK": [
        "keyboard", "mechanical keyboard", "gaming keyboard",
        "wireless keyboard", "bluetooth keyboard",
    ],
    "FR": [
        "clavier", "clavier mecanique", "clavier gaming",
        "clavier sans fil", "clavier bluetooth",
    ],
    "IT": [
        "tastiera", "tastiera meccanica", "tastiera gaming",
        "tastiera wireless", "tastiera bluetooth",
    ],
    "ES": [
        "teclado", "teclado mecanico", "teclado gaming",
        "teclado inalambrico", "teclado bluetooth",
    ],
}

def _get_discovery_search_terms(self) -> list:
    """Flache Liste aller Suchbegriffe, rotierend."""
    all_terms = []
    for market_terms in self.DISCOVERY_SEARCH_TERMS.values():
        all_terms.extend(market_terms)
    return all_terms
```

### Kategorie-IDs pro Markt

Bereits in `scripts/discover_bestseller_keyboards.py` definiert:

```python
FALLBACK_KEYBOARD_CATEGORIES = {
    3: [340843031],    # DE: Computer-Tastaturen
    2: [340831031],    # UK: Computer Keyboards
    4: [340843031],    # FR
    8: [340843031],    # IT
    9: [340843031],    # ES
}
```

Diese in `scheduler.py` importieren oder als Klassenvariable duplizieren.

---

## 7. Aufgabe 4: Seed-Datei befuellen

`data/seed_asins_eu_qwertz.txt` ist **LEER** und muss mit Top-ASINs befuellt werden.

### Quelle

Aus `data/keyboard_targets_1000.csv` die Top-100 ASINs extrahieren:
- Filter: `is_mismatch == True` ODER `confidence == "high"` ODER `detected_layout == "qwertz"`
- Sortiert nach: Confidence (high > medium > low), dann Brand bekannt

### Implementierung

```python
import csv
from pathlib import Path

csv_path = Path("data/keyboard_targets_1000.csv")
seed_path = Path("data/seed_asins_eu_qwertz.txt")

with open(csv_path) as f:
    rows = list(csv.DictReader(f))

# Priorisierung: Mismatches zuerst, dann high-confidence QWERTZ
priority = sorted(rows, key=lambda r: (
    r["is_mismatch"] != "True",      # Mismatches first
    r["confidence"] != "high",        # High confidence second
    r["detected_layout"] != "qwertz", # QWERTZ third
))

top_asins = []
seen = set()
for row in priority:
    asin = row["asin"]
    if asin not in seen:
        seen.add(asin)
        top_asins.append(asin)
    if len(top_asins) >= 100:
        break

with open(seed_path, "w") as f:
    f.write("# Top-100 QWERTZ Keyboard ASINs for Seed Discovery\n")
    f.write(f"# Generated: {datetime.now().isoformat()}\n")
    for asin in top_asins:
        f.write(f"{asin}\n")
```

---

## 8. Aufgabe 5: Config-Erweiterung

In `src/config.py`, neue Settings zur `Settings`-Klasse hinzufuegen:

```python
class Settings(BaseSettings):
    # ... bestehende Settings ...

    # Discovery Settings (NEU)
    discovery_enabled: bool = True
    discovery_interval_seconds: int = 1800      # 30 Minuten zwischen Discovery-Cycles
    discovery_max_tokens_per_hour: int = 200     # Token-Budget fuer Discovery
    discovery_token_reserve: int = 50            # Mindest-Reserve fuer Price-Checks
    discovery_batch_size: int = 20               # ASINs pro Validierungs-Batch
    discovery_search_terms_file: str = ""        # Optional: externe Suchbegriff-Datei
```

Und in `.env` die Defaults dokumentieren:

```bash
# Discovery Settings
DISCOVERY_ENABLED=true
DISCOVERY_INTERVAL_SECONDS=1800
DISCOVERY_MAX_TOKENS_PER_HOUR=200
DISCOVERY_TOKEN_RESERVE=50
```

---

## 9. Verifikation

### Vor dem Start

- [ ] `python -c "from src.config import get_settings; s = get_settings(); print(s.discovery_enabled)"` → `True`
- [ ] `wc -l data/seed_asins_eu_qwertz.txt` → mindestens 100 Zeilen
- [ ] `python -c "from scripts.collect_1000_keyboards import detect_layout; print(detect_layout({'title': 'QWERTZ Tastatur'}))"` → `{'detected_layout': 'qwertz', ...}`

### Nach dem Start

- [ ] Scheduler-Log zeigt: `"Discovery Cycle #0: X new ASINs (Strategy 0)"`
- [ ] `wc -l data/keyboard_targets_1000.csv` waechst ueber Zeit
- [ ] Keine `429 Rate Limit` Fehler (Token-Budget-Logik funktioniert)
- [ ] `python -c "import csv; rows=list(csv.DictReader(open('data/keyboard_targets_1000.csv'))); print(len(rows))"` → steigt

### Ziel-Metriken nach 24h

| Metrik | Jetzt | Ziel |
|--------|-------|------|
| Unique ASINs | 1.205 | 5.000+ |
| Mismatches | 117 | 500+ |
| Maerkte | nur DE | DE, UK, FR, IT, ES |
| Discovery Source | manuell | 3 automatische Strategien |

---

## 10. Zusammenfassung: Was du tun musst

```
1. Lies diese Datei komplett ✓
2. Erweitere src/config.py um Discovery-Settings (Aufgabe 5)
3. Fuelle data/seed_asins_eu_qwertz.txt mit Top-100 ASINs (Aufgabe 4)
4. Fuege run_asin_discovery() + Hilfsmethoden zum Scheduler hinzu (Aufgabe 1)
5. Importiere Layout-Detection aus collect_1000_keyboards.py
6. Fuege DISCOVERY_SEARCH_TERMS als Klassenvariable hinzu (Aufgabe 3)
7. Beachte Token-Budget (Aufgabe 2) — Discovery NUR wenn genug Tokens
8. Teste: python -c "from src.scheduler import PriceMonitorScheduler"
9. Committed alles mit aussagekraeftiger Message
```

### Reihenfolge ist wichtig!

Config zuerst (5) → Seed-File (4) → Scheduler-Code (1, 3) → Token-Logik (2) → Test → Commit

---

## 11. Architektur-Diagramm

```
                         Scheduler (run_scheduler)
                              |
              +-----------+---+-----------+
              |           |               |
        run_price_check  collect_deals   run_asin_discovery  <-- NEU
         (800 tok/h)     (100 tok/h)      (200 tok/h)
              |                               |
              v                    +----------+----------+
         Keepa API                 |          |          |
         (keepa_api.py)      search_     bestsellers  product_
                            products()                finder()
                                |          |          |
                                +----+-----+----------+
                                     |
                                Dedup gegen existing_asins (Set)
                                     |
                                Layout Detection (4 Layer)
                                     |
                                Append zu keyboard_targets_1000.csv
```

---

## 12. Bekannte Fallen

1. **CSV hat Kommas in Titeln** — IMMER `csv.DictReader/DictWriter` nutzen, nie manuell parsen
2. **`keepa_client.py` vs `keepa_api.py`** — Zwei verschiedene Clients! `keepa_client.py` = Light (httpx, guenstig), `keepa_api.py` = Heavy (keepa Library). Discovery nutzt den **Light Client** (`keepa_client.py`)
3. **Import-Pfade** — `collect_1000_keyboards.py` nutzt `importlib.util` statt normale Imports. Im Scheduler besser direkte Imports nutzen
4. **`detect_layout()` braucht ein Dict** mit Keys: `title`, `brand`, `ean`, `present_markets`, `description`, `features`. Neue Discovery-ASINs haben nur `title` — die anderen Felder leer lassen
5. **Rate Limiting** — Keepa gibt 429 zurueck. Der Light Client hat Retry mit Exponential Backoff (tenacity). Trotzdem Token-Budget beachten
6. **Seed-File Format** — Eine ASIN pro Zeile, Zeilen mit `#` sind Kommentare, nur 10-stellige Strings die mit `B` anfangen
7. **CSV Append** — Beim Appenden KEINEN Header nochmal schreiben. `csv.DictWriter` mit `writerow()` (nicht `writeheader()`)
