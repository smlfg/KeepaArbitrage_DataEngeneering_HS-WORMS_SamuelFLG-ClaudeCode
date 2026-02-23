# Pipeline Flow — Der Lebensweg eines Deals

Schritt-fuer-Schritt Erklaerung: Wie fliesst ein Deal von der Keepa API bis in Elasticsearch und die Datenbank?

> **Hinweis:** Diese Doku beschreibt die **deployed Version** (Input-Repo).
> Dateipfade weichen vom Main-Repo ab — siehe Mapping am Ende.

---

## Ueberblick (30 Sekunden Version)

```
⏰ APScheduler (alle 3600s)
   │
   ▼
📋 _load_targets() ← CSV/JSON/Env/Defaults
   │
   ▼
🔍 keepa_client.get_products_with_deals()
   │   HTTP GET → api.keepa.com/product
   │   ← Preise als CSV-Arrays (in Cent)
   │   → Rabatt berechnen, nur >10% behalten
   │
   ▼
🏷️ Keyboard-Filter + Layout-Erkennung
   │
   ├──▼ elasticsearch_service.index_deals()    → "keepa-deals"
   ├──▼ _calculate_arbitrage()                 → "keepa-arbitrage"
   └──▼ KafkaProducer.publish_deals()          → optional
```

---

## Phase 0: Der Wecker klingelt

Alle **3600 Sekunden** (1 Stunde) weckt APScheduler die Funktion `_collect_to_elasticsearch()`.

**Dateien:**
- `src/scheduler.py:916-922` — APScheduler konfiguriert den Interval-Job
- `src/scheduler.py:579` — `_collect_to_elasticsearch()` wird aufgerufen

```python
# scheduler.py:916-922
self.scheduler.add_job(
    func=self.orchestrator._collect_to_elasticsearch,
    trigger=IntervalTrigger(seconds=interval_secs, timezone="UTC"),
    id="elasticsearch_collection",
    name=f"Elasticsearch Deal Collection ({interval_secs}s)",
)
```

**Analogie:** Ein Wecker der sagt "Hey, schau mal ob es neue Deals gibt!"

---

## Phase 1: Wen fragen wir? (Targets laden)

**Datei:** `src/scheduler.py:382-577` — `_load_targets()`

Der Scheduler muss wissen: **welche ASINs** soll ich pruefen, und **in welchem Land**?

### Prioritaets-Kette

```
1. TXT-Datei  (data/seed_asins_eu_qwertz.txt)      ← bevorzugt
2. Env-Var    (DEAL_SEED_ASINS=B07W6JN8V8,B09H1BQXYF)
3. JSON-Datei (data/seed_asins_eu_qwertz.json)
4. Defaults   (3 hartcodierte ASINs)
```

### Ergebnis

Eine Liste von Target-Dicts:

```python
[
    {"asin": "B07W6JN8V8", "domain_id": 3, "market": "DE"},
    {"asin": "B07W6JN8V8", "domain_id": 4, "market": "FR"},
    {"asin": "B09H1BQXYF", "domain_id": 8, "market": "IT"},
    ...
]
```

### Hot-Reload

Wenn du die Seed-Datei aenderst waehrend der Scheduler laeuft, merkt er das automatisch (`scheduler.py:401-408` — vergleicht `mtime` der Datei). Kein Neustart noetig!

```python
# scheduler.py:401-408
if csv_path.exists():
    current_mtime = os.path.getmtime(csv_path)
    if self._cached_targets and current_mtime == self._last_seed_mtime:
        return self._cached_targets  # Cache treffer!
    if current_mtime > self._last_seed_mtime:
        self._cached_targets = []    # Cache invalidieren
```

### DE-Fallback

Falls keine DE-Targets in der Seed-Datei stehen, werden automatisch 5 Standard-QWERTZ-ASINs hinzugefuegt (`scheduler.py:486-509`).

---

## Phase 2: Keepa fragen (API Call)

**Datei:** `src/services/keepa_api.py:331-511` — `get_products_with_deals()`

### Batching & Domain-Gruppierung

Die Targets werden nach Domain gruppiert und in Batches an Keepa geschickt (default: max 10, konfigurierbar via `deal_scan_batch_size`):

```python
# scheduler.py:617-645
targets_by_domain = {
    3: ["B07W6JN8V8", "B09H1BQXYF", ...],  # DE
    4: ["B07W6JN8V8", ...],                  # FR
    8: ["B09H1BQXYF", ...],                  # IT
}

for domain_id, asins in targets_by_domain.items():
    for batch in chunks(asins, batch_size):
        deals = await keepa_client.get_products_with_deals(batch, domain_id)
```

### Der HTTP Request

```python
# keepa_api.py:356-370
# HTTP GET: https://api.keepa.com/product?key=XXX&domain=3&asin=B07W6JN8V8,B09H1BQXYF
params = {
    "key": self.api_key,
    "domain": domain_id,          # 3 = DE, 4 = FR, 8 = IT, 9 = ES
    "asin": ",".join(asins),      # Komma-getrennt
}
response = await self._make_request("product", params)
```

### Keepa-Antwort: Das CSV-Format

Keepa antwortet mit einem `products` Array. Jedes Produkt hat ein `csv` Feld — ein **Array von Preis-Arrays**:

```
csv[0]  = Amazon-Preis-Historie     [timestamp, preis, timestamp, preis, ...]
csv[1]  = Neuer 3rd-Party-Preis
csv[2]  = Gebraucht-Preis
csv[9]  = Warehouse Deal (WHD)      ← das Gold!
```

**Wichtig:** Preise sind in **Cent** gespeichert. `3999` = 39,99€. `-1` = "kein Preis verfuegbar".

### Preis-Extraktion

```python
# keepa_api.py:312-329 — _get_latest_price()
# Nimm das LETZTE Element des Arrays (= aktuellster Preis)
price_int = csv_array[-1]     # z.B. 3999
if price_int == -1:
    return None               # Preis nicht verfuegbar
return price_int / 100.0      # → 39.99€
```

Vier Preis-Typen werden extrahiert:
```python
# keepa_api.py:388-407
amazon_price = _get_latest_price(csv_data[0])   # Amazon selbst
new_price    = _get_latest_price(csv_data[1])   # Neuer Marketplace-Preis
used_price   = _get_latest_price(csv_data[2])   # Gebraucht
whd_price    = _get_latest_price(csv_data[9])   # Warehouse Deal
```

### Deal-Erkennung

```python
# keepa_api.py:446-474
# Preis-Hierarchie: WHD > Used > New
if whd_price:     deal_price = whd_price;  deal_type = "WHD"
elif used_price:  deal_price = used_price; deal_type = "Used"
elif new_price:   deal_price = new_price;  deal_type = "New"

# Referenzpreis: Amazon > New > Used > WHD
list_price = amazon_price or new_price or used_price or whd_price

# Rabatt berechnen
discount_pct = int((1 - deal_price / list_price) * 100)
# Beispiel: (1 - 39.99 / 59.99) * 100 = 33%

# Nur Deals mit genuegend Rabatt behalten
if discount_pct < min_discount:  # default: 10%
    continue
```

### Ergebnis pro Deal

```python
{
    "asin": "B07W6JN8V8",
    "title": "Logitech K380 QWERTZ",
    "current_price": 39.99,         # der Deal-Preis
    "list_price": 59.99,            # der "normale" Preis
    "discount_percent": 33,
    "rating": 4.5,
    "domain": "DE",
    "deal_type": "WHD",             # Warehouse Deal
    "source": "product_heuristic"   # wir nutzen /product, nicht /deals
}
```

---

## Phase 3: Filtern (Ist es eine Tastatur?)

**Datei:** `src/scheduler.py:591-708`

Keepa gibt alle Produkte zurueck die zu der ASIN passen — aber manche davon sind keine Tastaturen (Adapter, Kabel, etc.). Zwei Filter:

### Filter 1: Keyboard Keywords

```python
# scheduler.py:591-610
KEYBOARD_KEYWORDS = [
    "tastatur",          # DE
    "clavier",           # FR
    "tastiera",          # IT
    "teclado",           # ES
    "keyboard",          # EN
    "qwertz", "azerty",  # Layout-Signale
    "mechanisch",        # Mechanisch (DE)
    "keychron", "ducky", # Marken die immer Keyboards sind
]

# scheduler.py:692-697
keyboard_deals = [d for d in all_deals
                  if any(kw in d["title"].lower() for kw in KEYBOARD_KEYWORDS)]
```

**Logik:** Wenn der Titel eines der Keywords enthaelt → behalten. Sonst → raus.

### Filter 2: Layout-Erkennung

```python
# scheduler.py:330-380 — _detect_layout()
# Schritt 1: Explizite Signale im Titel suchen
"Logitech K380 QWERTZ"     → "QWERTZ"     (hoechste Konfidenz)
"Clavier AZERTY"            → "AZERTY"

# Schritt 2: Fallback nach Markt
domain="DE" ohne Signal     → "QWERTZ"     (niedrigere Konfidenz)
domain="FR" ohne Signal     → "AZERTY"
domain="IT" ohne Signal     → "QWERTY-IT"
```

Jeder Deal bekommt ein `layout` Feld bevor er gespeichert wird:
```python
# scheduler.py:646-649
for d in deals:
    d["layout"] = self._detect_layout(d.get("title", ""), d.get("domain", ""))
```

---

## Phase 4: In Elasticsearch speichern

**Datei:** `src/services/elasticsearch_service.py:94-256` — `index_deals()`

```python
# scheduler.py:729
result = await self.elasticsearch_service.index_deals(valid_deals)
```

### Schritt 4a: Index-Existenz pruefen

```python
# elasticsearch_service.py:41-92 — create_index()
# Falls "keepa-deals" nicht existiert → erstellen mit Mapping:
{
    "asin":             "keyword",    # exakte Suche
    "title":            "text",       # Volltextsuche
    "current_price":    "float",
    "discount_percent": "integer",
    "rating":           "float",
    "domain":           "keyword",
    "layout":           "keyword",
    "collected_at":     "date",
    ...
}
```

### Schritt 4b: Preis-Validierung

```python
# elasticsearch_service.py:117-131
# Deals ohne Preis rauswerfen
for deal in deals:
    current_price = deal.get("current_price")
    if current_price is not None and current_price > 0:
        valid_deals.append(deal)
```

*Das erklaert die 72 fehlerhaften Dokumente — sie sind VOR dieser Validierung reingekommen.*

### Schritt 4c: Bulk-Index

```python
# elasticsearch_service.py:141-195
# Fuer jeden Deal ein ES-Dokument bauen
doc = {
    "asin": "B07W6JN8V8",
    "title": "Logitech K380 QWERTZ",
    "current_price": 39.99,
    "discount_percent": 33,
    "domain": "DE",
    "layout": "QWERTZ",
    "collected_at": "2026-02-20T14:00:00",
}

# Alle auf einmal rein (bulk = schneller als einzeln)
success, errors = await async_bulk(
    client, actions,
    chunk_size=500,
    pipeline="keepa-pipeline"    # ← Ingest Pipeline
)
```

### Schritt 4d: Ingest Pipeline

```python
# elasticsearch_service.py:452-473 — _setup_ingest_pipeline()
# ES fuegt automatisch hinzu:
"processed_at":     "2026-02-20T14:00:01",    # Wann verarbeitet
"pipeline_version": "v1",                      # Pipeline-Version
"layout":           "QWERTZ"                   # → uppercase
```

---

## Phase 5: Arbitrage berechnen

**Datei:** `src/scheduler.py:764-864` — `_calculate_arbitrage()`

### Idee

Das gleiche Produkt kostet in Italien 30€ und in Deutschland 50€. Wenn Versand 7€ kostet → 13€ potentieller Gewinn.

### Versandkosten-Tabelle

```python
# scheduler.py:770-781
SHIPPING = {
    ("IT", "DE"): 6.0,     ("ES", "DE"): 10.0,
    ("FR", "DE"): 7.0,     ("UK", "DE"): 9.0,
    ("IT", "ES"): 8.0,     ("FR", "ES"): 8.0,
    ("IT", "FR"): 7.0,     ("UK", "FR"): 8.0,
}
MIN_MARGIN = 15.0   # Minimum 15€ Gewinn
```

### Algorithmus

```python
# scheduler.py:784-861
# 1. Alle Deals aus ES lesen (max 1000)
response = await es.client.search(index="keepa-deals", body={
    "size": 1000,
    "query": {"bool": {"filter": [{"exists": {"field": "current_price"}}]}}
})

# 2. Nach ASIN gruppieren
by_asin = {
    "B07W6JN8V8": [
        {"domain": "IT", "current_price": 30.00},
        {"domain": "DE", "current_price": 50.00},
    ]
}

# 3. Fuer jede ASIN mit >1 Markt: Guenstigste vs. Teuerste
entries_sorted = sorted(entries, key=lambda x: x["current_price"])
cheapest = entries_sorted[0]      # IT: 30€
expensive = entries_sorted[-1]    # DE: 50€

# 4. Margin berechnen
shipping = SHIPPING[("IT", "DE")]  # 6€
margin = 50 - 30 - 6              # = 14€

# 5. Nur wenn margin >= 15€ → Opportunity speichern
if margin >= MIN_MARGIN:
    opportunities.append({
        "asin": "B07W6JN8V8",
        "buy_domain": "IT",  "sell_domain": "DE",
        "buy_price": 30.00,  "sell_price": 50.00,
        "margin_eur": 14.00, "margin_pct": 66.7,
        "shipping_cost": 6.00,
    })
```

### Ergebnis

Opportunities werden in einen separaten ES-Index `keepa-arbitrage` geschrieben (`elasticsearch_service.py:364-450`). Document-ID = `{asin}_{buy_domain}_{sell_domain}` → automatisches Upsert.

---

## Phase 6: Kafka (optional, fire-and-forget)

**Datei:** `src/scheduler.py:749-759`

```python
# Kafka publish — nicht kritisch, ES ist der primaere Speicher
try:
    _producer = KeepaKafkaProducer(settings.kafka_bootstrap_servers)
    await _producer.start()
    published = await _producer.publish_deals(valid_deals)
    await _producer.stop()
    logger.info(f"📨 Kafka: {published} deals → keepa-raw-deals")
except Exception as e:
    logger.warning(f"Kafka publish skipped: {e}")
```

**Wichtig:** Wenn Kafka nicht laeuft → nur ein Warning, kein Crash. Elasticsearch ist der primaere Datenspeicher fuer Deals.

---

## Fehlerbehandlung

| Phase | Was passiert bei Fehler? |
|-------|--------------------------|
| Targets laden | Fallback auf naechste Prioritaet (TXT → Env → JSON → Defaults) |
| Keepa API | Rate Limit → 60s warten, 1x Retry. Dann Domain ueberspringen |
| Keepa 404 | `NoDealAccessError` → kompletter Abbruch (API-Plan unterstuetzt Endpoint nicht) |
| ASIN ohne Preis | Wird uebersprungen (`continue`), kein Crash |
| ES nicht erreichbar | `ConnectionError` → Log + Return, kein Crash |
| ES Bulk-Fehler | Partielle Erfolge moeglich. Errors werden geloggt |
| Arbitrage-Fehler | `try/except` → Warning, Scheduler laeuft weiter |
| Kafka down | `try/except` → Warning, Scheduler laeuft weiter |

---

## Timing

```
Ein typischer Durchlauf:

_load_targets()                    ~0.01s  (Datei lesen)
get_products_with_deals() × N     ~2-10s  (Keepa API, abhaengig von Batch-Groesse)
Keyboard-Filter                    ~0.001s (String-Matching)
index_deals()                      ~0.5-2s (ES Bulk)
_calculate_arbitrage()             ~0.3-1s (ES Query + Berechnung)
publish_deals()                    ~0.1s   (Kafka, wenn verfuegbar)
────────────────────────────────────────
Gesamt:                            ~3-15s pro Zyklus
```

---

## Dateien-Referenz

### Deployed Version (Input-Repo)

| Datei | Rolle in der Pipeline |
|-------|----------------------|
| `src/scheduler.py` | Orchestrierung: Targets laden, Keepa aufrufen, filtern, indexieren |
| `src/services/keepa_api.py` | Keepa API: HTTP Requests, CSV-Parsing, Preis-Extraktion |
| `src/services/elasticsearch_service.py` | ES: Index erstellen, Bulk-Index, Arbitrage-Index |
| `src/services/kafka_producer.py` | Kafka: Deals als Messages publizieren (optional) |
| `src/utils/pipeline_logger.py` | Structured Logging: JSON-Events fuer jede Pipeline-Phase |
| `src/config.py` | Konfiguration: API-Keys, Intervals, Batch-Groessen |
| `data/seed_asins_eu_qwertz.txt` | Input: Welche ASINs in welchen Maerkten pruefen |

### Pfad-Mapping: Main-Repo vs. Input-Repo

| Main-Repo | Input-Repo (deployed) | Unterschied |
|-----------|-----------------------|-------------|
| `src/scheduler.py` | `src/services/scheduler.py` | Flacher vs. nested Pfad |
| `src/services/keepa_api.py` | `src/services/keepa_client.py` | Anderer Dateiname |
| `data/seed_asins_eu_qwertz.txt` | `data/seed_targets_eu_qwertz.csv` | TXT vs. CSV als Primary |
| `PriceMonitorScheduler` | `DealOrchestrator` + `DealScheduler` | Andere Klassen |
| `_load_seed_asins_from_file()` | `_load_targets()` | Einfacher vs. Prioritaets-Kette |
| `deal_scan_batch_size=10` | `deal_scan_batch_size=50` | Gleicher Default (10), Input-Repo ueberschreibt auf 50 |
