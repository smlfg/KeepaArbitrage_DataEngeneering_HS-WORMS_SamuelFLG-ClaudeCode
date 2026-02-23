# Keepa API — Referenz fuer das Keeper System

## Was ist Keepa?

Keepa ist ein Amazon-Preisverfolgungs-Service. Die API liefert historische und aktuelle Preisdaten fuer Amazon-Produkte. Wir nutzen sie als **einzige Datenquelle** fuer Preis-Monitoring und Deal-Erkennung.

**Unser Plan:** Nur `/product` und `/token` funktionieren. Die Endpoints `/query`, `/search`, `/bestsellers` geben 404/500 zurueck. Das ist kein Bug — unser API-Plan hat keinen Zugang zu diesen Endpoints.

---

## Endpoints die wir nutzen

### `/product` — Produktdaten abrufen

**Kosten:** ~15 Tokens pro ASIN
**Datei:** `src/services/keepa_api.py` → `KeepaAPIClient.query_product()`

```python
# So rufen wir Produktdaten ab:
products = self._api.query(asin, domain="DE", stats=90, history=True, offers=20)
```

**Was kommt zurueck:**

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| `title` | str | Produktname |
| `csv` | list[list] | Preis-History-Arrays (Keepa-Format, siehe unten) |
| `stats` | dict | Aggregierte Statistiken (90-Tage) |
| `offers` | list | Aktuelle Angebote von Haendlern |
| `rating` | float | Bewertung (1.0 - 5.0, manchmal *10) |
| `categories` | list | Amazon-Kategorie-IDs |
| `buyBoxPrice` | int | Buy-Box-Preis in Cents |

### `/token` — API-Status pruefen

**Kosten:** 0 Tokens (gratis)

```python
import httpx
r = httpx.get(f"https://api.keepa.com/token?key={api_key}")
# Response: {"tokensLeft": 150, "refillIn": 45, "refillRate": 5, ...}
```

Nützlich fuer Preflight-Checks und Monitoring.

---

## Keepa CSV-Format (das Herzstück)

Keepa speichert Preise NICHT als einfache Zahlen, sondern als **verschachtelte Arrays**. Das ist das wichtigste Konzept:

```
csv[index] = [keepa_time, price_cents, keepa_time, price_cents, ...]
```

### CSV-Index-Referenz

| Index | Bedeutung | Beispiel |
|-------|-----------|---------|
| 0 | Amazon-Preis | Direkt von Amazon verkauft |
| 1 | Marketplace Neu | Guenstigster neuer 3rd-Party |
| 2 | Marketplace Gebraucht | Guenstigster gebrauchter |
| 3 | Sales Rank | Verkaufsrang (KEIN Preis!) |
| 4 | Listenpreis (UVP) | Herstellerpreis |
| 5 | Collectible | Sammlerstueck |
| 6 | Refurbished | Generalueberholt |
| 7 | New FBA | Neuer Preis via Fulfillment by Amazon |
| 8 | Lightning Deals | Blitzangebote |
| 9 | Warehouse Deals | Amazon Warehouse |
| 10 | New FBM Shipping | Versandkosten Marketplace |
| 11 | **Buy Box** | Wer die Buy Box hat |
| 12 | Used Like New | Gebraucht - Wie neu |
| 13 | Used Very Good | Gebraucht - Sehr gut |
| 14 | Used Good | Gebraucht - Gut |
| 15 | Used Acceptable | Gebraucht - Akzeptabel |
| 16 | **Rating** | Bewertung (Wert / 10.0) |
| 17 | **Review Count** | Anzahl Bewertungen |
| 18 | Buy Box Used | Buy Box fuer Gebrauchte |
| 19 | Sales Rank Drops (30d) | Verkaufsrang-Aenderungen |

### Spezialwerte

- **-1** = Nicht verfuegbar (z.B. Amazon verkauft das Produkt nicht direkt)
- **-2** = Keine Daten (Keepa hat nie einen Preis erfasst)
- **Preise in Cents!** → Immer durch 100 teilen fuer EUR/USD

### Unsere Preis-Prioritaet

Wir suchen den "besten" aktuellen Preis in dieser Reihenfolge:

```
Amazon(0) > Buy Box(11) > New FBA(7) > New 3rd(1) > Used Like New(12) > Buy Box Used(18) > Warehouse(9)
```

Code: `keepa_api.py:466-474`

---

## Domain-IDs

Keepa nutzt numerische IDs statt Laendernamen:

| ID | Land | Amazon-Domain |
|----|------|---------------|
| 1 | US | amazon.com |
| 2 | **GB** | amazon.co.uk |
| 3 | **DE** | amazon.de |
| 4 | **FR** | amazon.fr |
| 5 | JP | amazon.co.jp |
| 6 | CA | amazon.ca |
| 8 | **IT** | amazon.it |
| 9 | **ES** | amazon.es |
| 10 | IN | amazon.in |

**Fett** = Maerkte die wir aktiv ueberwachen (EU QWERTZ-Fokus).

---

## Token Bucket Rate Limiting

Die Keepa API hat ein Token-Budget. Jeder API-Call kostet Tokens, und Tokens werden ueber Zeit aufgefuellt.

### Wie es funktioniert

```
┌─────────────────────────────────────────┐
│  Token Bucket (AsyncTokenBucket)        │
│                                         │
│  tokens_available: 150                  │
│  tokens_per_minute: 200                 │
│  refill_interval: 60s                   │
│                                         │
│  API Call kommt rein (kostet 15)        │
│  → tokens_available >= 15? ✓            │
│  → tokens_available -= 15  → 135        │
│  → API Call ausfuehren                  │
│                                         │
│  Naechster Call (kostet 15)             │
│  → tokens_available >= 15? ✓            │
│  → Weiter...                            │
│                                         │
│  Nach 60s: tokens_available = 200 (Refill)│
└─────────────────────────────────────────┘
```

### Token-Kosten pro Endpoint

| Endpoint | Kosten | Unser Nutzungsfall |
|----------|--------|--------------------|
| `query` (product) | ~15 | Preis-Check, ASIN-Validierung |
| `deals` | ~5 | Deal-Suche (404 auf unserem Plan) |
| `category` | ~5 | Nicht genutzt |
| `best_sellers` | ~3 | Nicht genutzt |

### Timeout-Verhalten

```python
# Wenn keine Tokens verfuegbar:
# 1. Warte bis zu 120 Sekunden
# 2. Pruefe alle 5 Sekunden ob Tokens verfuegbar
# 3. Nach 120s: TokenInsufficientError
await self._token_bucket.wait_for_tokens(cost=15, max_wait=120.0)
```

---

## Fehlerbehandlung

| Exception | Bedeutung | Was tun |
|-----------|-----------|---------|
| `InvalidAsin` | ASIN ist nicht 10 Zeichen | ASIN pruefen |
| `TokenLimitError` | Kein Token-Budget | Warten, spaeter erneut versuchen |
| `TokenInsufficientError` | Timeout beim Warten auf Tokens | Intervall erhoehen |
| `NoDealAccessError` | Deals-Endpoint nicht im Plan | Normal fuer uns — Fallback nutzen |
| `KeepaAPIError` | Allgemeiner API-Fehler | Logs pruefen, ggf. API-Key checken |

---

## Deals API (Theorie — funktioniert NICHT auf unserem Plan)

Falls wir mal upgraden: So wuerde die Deals API funktionieren:

```python
deal_params = {
    "page": 0,
    "domainId": 3,                         # DE
    "includeCategories": [340843031],       # Tastaturen
    "deltaPercentRange": [20, 90],          # 20-90% Rabatt
    "currentRange": [1500, 30000],          # 15-300 EUR (in Cents!)
    "hasReviews": True,
    "isFilterEnabled": True,
    "isRangeEnabled": True,
}
result = self._api.deals(deal_params, domain="DE")
# Result: {"dr": [...deals...], "categoryNames": [...]}
```

### Deal-Daten-Struktur

Deals nutzen das gleiche `current[]` Array wie Products:
- `current[0]` = Amazon-Preis (Cents)
- `current[7]` = New FBA (Cents)
- `current[1]` = New 3rd Party (Cents)
- `current[16]` = Rating (/10)
- `current[17]` = Review Count

---

## Unsere Fallback-Strategie

Da die Deals API nicht funktioniert:

```
1. Seed-ASIN-Pool laden (data/seed_asins_eu_qwertz.txt)
2. Jede ASIN via /product abfragen
3. Preis, Rating, Sales Rank extrahieren
4. Deal-Score berechnen (discount * rating * 1/rank)
5. Ergebnis indexieren (ES + Kafka + PostgreSQL)
```

Datei: `src/scheduler.py:594-608` (`_collect_seed_asin_deals`)

---

## Konfiguration

In `.env`:
```
KEEPA_API_KEY=dein-api-key-hier
```

In `src/config.py`:
```python
keepa_api_key: str = ""
deal_source_mode: str = "product_only"    # "product_only" oder "deals"
deal_seed_asins: str = ""                  # Komma-getrennte ASINs
deal_seed_file: str = "data/seed_asins_eu_qwertz.txt"
deal_scan_interval_seconds: int = 3600     # 1 Stunde
deal_scan_batch_size: int = 10             # ASINs pro Batch
```

---

## Singleton-Pattern

Es gibt genau EINEN KeepaAPIClient im System:

```python
# In src/services/keepa_api.py
_keepa_client: Optional[KeepaAPIClient] = None

def get_keepa_client() -> KeepaAPIClient:
    global _keepa_client
    if _keepa_client is None:
        _keepa_client = KeepaAPIClient()
    return _keepa_client
```

Der Scheduler erstellt seinen eigenen Client (`self.keepa_client = KeepaAPIClient()`).

---

## Praxis-Tipps

1. **Immer Token-Status pruefen** bevor grosse Batch-Jobs laufen: `GET /token?key=...`
2. **Preise sind in Cents** — vergiss nie durch 100 zu teilen
3. **Rating ist manchmal *10** — `if rating > 10: rating /= 10`
4. **CSV-Arrays rueckwaerts lesen** fuer den aktuellsten Preis: `for i in range(len(arr)-1, 0, -2)`
5. **-1 und -2 sind keine Preise** — immer `if val > 0` pruefen
6. **Stats-Fallback nutzen** wenn CSV leer ist: `product["stats"]["current"][index]`
