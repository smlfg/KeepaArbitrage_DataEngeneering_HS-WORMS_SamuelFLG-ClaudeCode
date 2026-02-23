# Code Review: Keepa Amazon Price Monitoring System

**Reviewer:** Senior Data Engineer & Python Architekt  
**Datum:** 19.02.2026  
**Umfang:** Vollständiges Code-Review aller Kernkomponenten

---

## 1. Keepa API Nutzung

### Token-Kosten & Rate Limiting

Die Token-Kosten sind korrekt implementiert:
- `query`: 15 Tokens (Produktabfrage)
- `deals`: 5 Tokens (Deal-Suche)
- `category`: 5 Tokens
- `best_sellers`: 3 Tokens
- `seller`: 5 Tokens

Diese Werte entsprechen den offiziellen Keepa API Preisen.

### Token Bucket Implementierung

Die `AsyncTokenBucket` Klasse (keepa_api.py:98-213) ist **gut implementiert**:
- Refill-Logik basierend auf Zeitverlauf
- `wait_for_tokens()` mit configurablem Timeout und Check-Intervall
- Automatische Aktualisierung des Token-Status aus Keepa API (`update_token_status()`)

**Probleme:**
1. **Token-Verschwendung:** Der Token Bucket startet mit 20 Tokens, aber wenn die Keepa API mehr Tokens erlaubt (z.B. 45/min für Premium-Accounts), wird das erst nach dem ersten `update_token_status()` erkannt. Bei vielen Watches führt das zu unnötigen Wartezeiten.

2. **Keine Batch-Optimierung:** Die `query_product()` Methode fragt einzeln ab. Keepa erlaubt bis zu 10 ASINs pro Query (Batch-Query). Das wird nicht genutzt:
   ```python
   # Aktuell: 50 separate API-Calls für 50 Watches
   for watch in watches:
       result = await self.check_single_price(watch)  # 15 Tokens pro Call!
   ```

### Bewertung: 6/10
Token-Management funktioniert, aber Batch-Abfragen werden nicht genutzt.

---

## 2. Architektur & Struktur

### Ordnerstruktur

```
src/
├── services/          # Daten-Zugriff & externe APIs
│   ├── keepa_api.py
│   ├── database.py
│   ├── kafka_producer.py
│   ├── elasticsearch_service.py
│   └── notification.py (implizit)
├── agents/            # Business-Logik
│   ├── deal_finder.py
│   ├── price_monitor.py
│   └── alert_dispatcher.py
├── api/
│   └── main.py        # FastAPI Endpoints
├── scheduler.py       # Orchestrierung
└── config.py
```

**Bewertung: 8/10** - Logische und skalierbare Struktur.

### Separation of Concerns

| Komponente | Verantwortlichkeit |
|------------|-------------------|
| `keepa_api.py` | API-Kommunikation, Token-Management |
| `database.py` | SQLAlchemy Models, CRUD-Operationen |
| `kafka_producer.py` | Event-Streaming |
| `elasticsearch_service.py` | Such-Indexierung |
| `deal_finder.py` | Deal-Suche, Scoring, Spam-Filter |
| `price_monitor.py` | Preisüberwachung, Alert-Trigger |
| `alert_dispatcher.py` | Notification-Versand |
| `scheduler.py` | Orchestrierung, Cron-Jobs |

### Zirkuläre Abhängigkeiten

**Keine zirkulären Abhängigkeiten** - sauber importiert.

### Probleme:

1. **Zu viele Singleton-Instanzen:** `deal_finder`, `price_monitor`, `alert_dispatcher` sind als Module-Level-Singletons definiert. Das erschwert Testing und macht den Code weniger flexibel.

2. **Scheduler wird zweimal instanziiert:** In `scheduler.py:51` und `scheduler.py:590` werden unterschiedliche Instanzen erstellt.

---

## 3. Bottlenecks & Performance

> **Update 20.02.2026:** Mehrere der unten aufgeführten Issues wurden behoben.

### 3.1 Sequentielle API-Aufrufe

**scheduler.py** — `run_price_check()` nutzt BEREITS `asyncio.gather()` mit `Semaphore(5)` (Zeile ~278-310). Dies wurde bei der ursprünglichen Review übersehen.

**price_monitor.py** — `fetch_prices()` und `check_prices()` waren sequentiell. **RESOLVED 20.02.2026:** Beide Methoden nutzen jetzt `asyncio.gather()` + `Semaphore(5)`.

**scheduler.py** — `_collect_seed_asin_deals()` war sequentiell. **RESOLVED 20.02.2026:** Parallelisiert mit `asyncio.gather()` + `Semaphore(5)`.

**deal_finder.py:407-437** — `candidate_targets` Loop ist noch sequentiell, aber betrifft nur wenige Candidates pro Aufruf (typisch 1-3).

**Verbleibender Optimierungspunkt:** Keepa erlaubt bis zu 10 ASINs pro Batch-Query. Das wird noch nicht genutzt — könnte Token-Kosten um ~90% reduzieren.

### 3.2 N+1 Query Problem

**database.py:255-261:**
```python
async def get_active_watches() -> List[WatchedProduct]:
    result = await session.execute(
        select(WatchedProduct).where(WatchedProduct.status == WatchStatus.ACTIVE)
    )
    return result.scalars().all()
```

Hier werden nur Watch-Objekte geladen. In `run_price_check()` (scheduler.py:171) wird dann für jede Watch ein separates `update_watch_price()` aufgerufen, was wiederum einzelne Sessions öffnet. **Besser:** Batch-Update mit `bulk_update_mappings()`.

### 3.3 Redis wird nicht genutzt

**config.py:37:**
```python
redis_url: str = "redis://localhost:6379/0"
```

Redis ist konfiguriert aber **nie importiert oder genutzt**. Das ist verschwendete Infrastruktur.

### 3.4 Elasticsearch & Kafka - Over-Engineering?

Für ein Price-Monitoring-System mit <1000 Watches:
- **Kafka:** Wird für jede Preisänderung ein Event gesendet. Bei 1000 Watches alle 6 Stunden = ~170 Events/Tag. Kafka ist hier Overhead.
- **Elasticsearch:** Für Preis-History und Deal-Suche. Berechtigt, aber bei kleinen Datenmengen auch PostgreSQL ausreichend.

**Aber:** Für zukünftige Skalierung und Analytics ist die Architektur gut vorbereitet.

### 3.5 Deal-Indizierung passiert mehrfach

Dieselben Deals werden zu ES und Kafka gesendet in:
1. `run_daily_deal_reports()` (scheduler.py:327-372)
2. `collect_deals_to_elasticsearch()` (scheduler.py:473-524)
3. `deal_finder.run_daily_search()` (deal_finder.py:511-512)

**Bewertung: 7/10** - Die Haupt-Loops nutzen jetzt `asyncio.gather()`. Verbleibender Punkt: Keepa Batch-Queries (10 ASINs/Call).

---

## 4. Effizienz

### 4.1 Redundante Normalisierung

**deal_finder.py:** Die Methode `_normalize_deal()` wird mehrfach aufgerufen:
- In `filter_spam()` (Zeile 465)
- In `should_send_report()` (Zeile 489)
- In `_index_deal_to_elasticsearch()` (Zeile 546)
- In `search_deals()` (Zeile 444 für Scoring)

Kein Caching - bei 100 Deals × 4 Aufrufe = 400 redundante Normalisierungen.

### 4.2 Batch-Sizes

- `PriceMonitorAgent.BATCH_SIZE = 50` - gut
- `DealCollector.batch_size` aus Config (default 50) - gut
- **Aber:** Die Batches werden sequentiell abgearbeitet, nicht parallel.

### 4.3 Memory Leaks

**alert_dispatcher.py:14:**
```python
def __init__(self):
    self.sent_alerts = {}  # Wächst unbegrenzt!
```

Diese Dict wird nie bereinigt. Bei vielen Alerts über lange Laufzeit → Memory Leak.

### 4.4 Datenstrukturen

**scheduler.py:235:**
```python
results["watches"].append(result)  # Alle Ergebnisse im Memory
```

Bei 10.000 Watches werden alle Ergebnisse im Memory gehalten. Sollte zu Kafka/DB gestreamt werden.

**Bewertung: 6/10**

---

## 5. Verständlichkeit & Wartbarkeit

### 5.1 Lesbarkeit

Der Code ist **gut lesbar** mit:
- PEP8-konformer Formatierung
- aussagekräftigen Variablennamen
- Logging an strategischen Stellen

### 5.2 Fehlende Kommentare/Docstrings

**Kritisch fehlende Docstrings:**
- `scheduler.py:run_price_check()` - 130 Zeilen ohne Docs
- `scheduler.py:collect_deals_to_elasticsearch()` - Background-Task ohne Beschreibung
- `database.py` - Models haben keine Docstrings

### 5.3 Magic Numbers

| Location | Magic Number | Bedeutung |
|----------|-------------|-----------|
| scheduler.py:45 | `21600` | 6 Stunden in Sekunden |
| scheduler.py:49 | `1.01` | 1% Preistoleranz |
| deal_finder.py:330 | `100000` | Default Sales Rank |
| price_monitor.py:28-32 | `2, 4, 6` | Check-Intervalle in Stunden |
| alert_dispatcher.py:11 | `[0, 30, 120]` | Retry-Delays in Sekunden |

### 5.4 Error-Handling

**Positiv:**
- Eigene Exception-Klassen (keepa_api.py:31-58)
- Fallback-Logik bei fehlgeschlagenen API-Calls
- Graceful Degradation wenn Kafka/ES nicht verfügbar

**Probleme:**
- `price_monitor.py:64`: `print()` statt Logging
- `alert_dispatcher.py:135-141`: Retry-Loop ist umständlich implementiert

### 5.5 Testbarkeit

- Singletons auf Module-Level erschweren Mocking
- Keine abstrakten Interfaces
- Abhängigkeiten werden direkt instanziiert statt injectiert

**Bewertung: 7/10**

---

## 6. Top 5 Verbesserungsvorschläge

### 1. **Batch-API-Aufrufe implementieren** (HIGH IMPACT)

Keepa erlaubt bis zu 10 ASINs pro Query. Das würde die Token-Kosten um 90% reduzieren.

```python
# Statt:
for asin in asins:
    await keepa_client.query_product(asin)

# Besser:
chunks = [asins[i:i+10] for i in range(0, len(asins), 10)]
results = await asyncio.gather(*[
    keepa_client.query_products_chunk(chunk) for chunk in chunks
])
```

**Impact:** 100 Watches: 75min → 8min Wartezeit

### 2. **asyncio.gather() für parallele Verarbeitung** (HIGH IMPACT)

```python
# Statt:
for watch in watches:
    result = await self.check_single_price(watch)

# Besser:
tasks = [self.check_single_price(watch) for watch in watches]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

### 3. **Redis-Cache für Token-Status oder Elasticache für Preise** (MEDIUM IMPACT)

Entweder Redis entfernen ODER effektiv nutzen:
- Token-Status cachen
- Aktuelle Preise cached halten um API-Calls zu reduzieren

### 4. **Alert-Deduplikation mit TTL** (LOW IMPACT, aber wichtig)

```python
# alert_dispatcher.py
import time

class AlertDispatcherAgent:
    def __init__(self):
        self.sent_alerts = {}
    
    def cleanup_old_alerts(self, max_age_seconds=3600):
        cutoff = time.time() - max_age_seconds
        self.sent_alerts = {
            k: v for k, v in self.sent_alerts.items() 
            if v > cutoff
        }
```

### 5. **Konstanten zentralisieren** (LOW IMPACT, aber Wartbarkeit)

```python
# src/constants.py
class SchedulerConstants:
    CHECK_INTERVAL_SECONDS = 21600  # 6 hours
    DEAL_SCAN_INTERVAL_SECONDS = 300  # 5 minutes
    
class KeepaConstants:
    BATCH_SIZE = 10
    DEFAULT_TOKEN_COST_QUERY = 15
    DEFAULT_TOKEN_COST_DEALS = 5

class PriceConstants:
    VOLATILE_THRESHOLD = 5.0
    STABLE_THRESHOLD = 2.0
    TARGET_PRICE_TOLERANCE = 1.01  # 1%
```

---

## Zusammenfassung

| Kategorie | Bewertung |
|-----------|-----------|
| Keepa API Nutzung | 6/10 |
| Architektur | 8/10 |
| Performance | 7/10 |
| Effizienz | 6/10 |
| Wartbarkeit | 7/10 |
| **Gesamt** | **6.8/10** |

**Fazit:** Das System hat eine **solide Architektur** und ist gut strukturiert. Die sequentiellen API-Aufrufe in `price_monitor.py` und `scheduler.py` wurden parallelisiert (20.02.2026). Verbleibende Optimierung: Keepa Batch-Queries und Redis-Nutzung. Mit Batch-Queries könnte die Token-Effizienz nochmals um ~90% steigen.

---

*Review erstellt am 19.02.2026*
