# Die 4 Schwerpunkte des Profs — Kompakt-Lernblatt

> Intel von Auriol: Der Prof achtet auf **Containers, Automatisierung, Datenbank, ETL-Schritte**.
> Dieses Blatt ergaenzt die KILLFRAGEN_DRILL.md mit der Tiefe die dort fehlt.

---

## 1. Containers — Alle 8 Services und ihre Abhaengigkeiten

### Service-Uebersicht

| # | Service | Image | Port | Aufgabe |
|---|---------|-------|------|---------|
| 1 | **app** | Custom (Dockerfile) | 8000 | FastAPI REST API — Swagger UI, CRUD-Endpoints |
| 2 | **db** | postgres:15-alpine | 5432 | Relationale Datenhaltung — ACID, Source of Truth |
| 3 | **zookeeper** | cp-zookeeper:7.5.0 | 2181 | Kafka-Koordination (Leader Election, Config) |
| 4 | **kafka** | cp-kafka:7.5.0 | 9092 | Event Streaming — Topics `price-updates`, `deal-updates` |
| 5 | **elasticsearch** | elasticsearch:8.11.0 | 9200 | Volltextsuche + Aggregationen |
| 6 | **kibana** | kibana:8.11.0 | 5601 | Dashboard-Visualisierung |
| 7 | **kibana-setup** | curlimages/curl:8.5.0 | - | Einmalige Kibana-Konfiguration (`restart: "no"`) |
| 8 | **scheduler** | Custom (gleicher Build) | - | Periodische Keepa-Abfragen, Deal-Collection |

### depends_on-Kette

```
zookeeper
    └── kafka
            └── app        (+ db, elasticsearch)
            └── scheduler  (+ db, elasticsearch)

elasticsearch
    └── kibana
        └── kibana-setup
```

**Warum Scheduler als eigener Container?** Separation of Concerns — ein Scheduler-Crash zieht die REST API nicht runter. Beide nutzen denselben Build (Dockerfile), aber verschiedene Entrypoints: `uvicorn` vs `python -m src.scheduler`.

### 4 Named Volumes fuer Persistenz

| Volume | Container | Was wird persistiert |
|--------|-----------|---------------------|
| `postgres_data` | db | Alle 7 Tabellen, Transaktionslogs |
| `kafka_data` | kafka | Event-Logs, 7 Tage Retention |
| `zookeeper_data` | zookeeper | Cluster-Metadata, Topic-Config |
| `elasticsearch_data` | elasticsearch | Suchindex, Kibana-Dashboards |

**Pruefungssatz:** "Wir haben 8 Docker-Services: app und scheduler teilen sich den Build aber laufen getrennt — Separation of Concerns. Kafka braucht Zookeeper, die App braucht DB + Kafka + ES. Die 4 Named Volumes sichern Daten ueber Container-Neustarts hinweg."

---

## 2. Automatisierung: Unser Scheduler vs Airflow

### Was unser Scheduler macht

```python
# src/scheduler.py — PriceMonitorScheduler.run_scheduler()
while self.running:
    await self.run_price_check()          # Phase A: Preise pruefen
    if cycle_count % 4 == 0:
        await self.run_daily_deal_reports() # Phase B: Deal-Reports (alle 24h)
    await asyncio.sleep(self.check_interval)  # 21600s = 6h
```

**4 Startup-Phasen (Reihenfolge wichtig!):**
1. **Kafka Producers** starten (muessen vor Consumers bereit sein)
2. **Elasticsearch** verbinden (unabhaengig von Kafka)
3. **Kafka Consumers** starten (brauchen laufende Producers)
4. **Deal Collector** als Background-Task launchen

### Konzeptueller Vergleich

| Aspekt | Unser Scheduler | Apache Airflow |
|--------|----------------|----------------|
| Scheduling | `asyncio.sleep(21600)` | Cron-basierter Scheduler |
| "DAG" | Lineare Kette: Extract → Transform → Load | Gerichteter azyklischer Graph |
| Monitoring | Logging (`logger.info`) | Web-UI mit Gantt-Charts |
| Backfill | Nicht noetig (Echtzeit-Preise) | Historische Runs nachholen |
| Dependencies | Startup-Phasen im Code | Deklarativ pro Task definiert |
| Retry | `restart: unless-stopped` (Docker-Level) | Task-Level Retry mit Backoff |
| Konfiguration | Environment Variables | Airflow Variables + Connections |

### Warum das fuer uns reicht

Unser Use Case ist **eine lineare Pipeline** die alle 6 Stunden laeuft. Kein Backfill noetig (Keepa liefert nur aktuelle Preise), keine komplexen Abhaengigkeiten zwischen Tasks, kein Multi-Team-Betrieb der ein UI braucht.

**Pruefungssatz:** "Unser Scheduler macht dasselbe wie Airflow fuer diesen Use Case — periodische Ausfuehrung mit konfigurierbarem Intervall. Airflow waere noetig wenn wir komplexe DAG-Abhaengigkeiten, Backfill oder ein Monitoring-UI brauchten."

---

## 3. Datenbanktabellen — Alle 7 mit WARUM

> Quelle: `src/services/database.py` — SQLAlchemy Models mit async PostgreSQL

### Die 7 Tabellen

| # | Tabelle | Wichtigste Spalten | WARUM diese Tabelle existiert |
|---|---------|-------------------|-------------------------------|
| 1 | **users** | `email`, `telegram_chat_id`, `discord_webhook`, `is_active` | Multi-Channel Notifications — ein User, drei moegliche Kanaele |
| 2 | **watched_products** | `asin`, `target_price`, `current_price`, `status` (ACTIVE/PAUSED/INACTIVE), `volatility_score` | Kern der Preisueberwachung — was wird beobachtet und ab welchem Preis wird alertet |
| 3 | **price_history** | `watch_id` (FK), `price`, `buy_box_seller`, `recorded_at` | Preisverlauf tracken — ohne History kein Trend, kein Volatility-Score |
| 4 | **price_alerts** | `triggered_price`, `target_price`, `status` (PENDING→SENT→FAILED), `notification_channel` | Alert-Lifecycle — wann wurde alertet, ueber welchen Kanal, wurde es zugestellt |
| 5 | **deal_filters** | `categories` (JSON), `min_discount`, `max_discount`, `min_rating`, `min_price`, `max_price` | User-spezifische Deal-Kriterien — jeder User definiert was fuer ihn ein "guter Deal" ist |
| 6 | **deal_reports** | `filter_id` (FK), `deals_data` (JSON), `generated_at`, `sent_at` | Generierte Deal-Berichte — Verlauf wann welcher Report an wen ging |
| 7 | **collected_deals** | `asin`, `current_price`, `discount_percent`, `deal_score`, `rating`, `sales_rank`, `prime_eligible` | Rohdaten-Archiv — alle gesammelten Deals mit Scoring fuer historische Analyse |

### Beziehungen (Entity-Relationship)

```
User (1) ──→ (N) WatchedProduct ──→ (N) PriceHistory
                                  ──→ (N) PriceAlert

User (1) ──→ (N) DealFilter ──→ (N) DealReport

CollectedDeal (eigenstaendig — Rohdaten-Archiv, keine FK zu User)
```

### Warum diese Struktur?

- **Normalisierung:** User-Daten nicht in jeder Watch dupliziert → 3. Normalform
- **Soft Delete:** `WatchStatus.INACTIVE` statt echtem DELETE → Daten bleiben erhalten
- **JSON-Spalten:** `categories` in DealFilter und `deals_data` in DealReport sind JSON — flexibel ohne Schema-Migration
- **Composite Index:** `idx_watched_products_user_asin` (UNIQUE) verhindert doppelte Watches pro User
- **Zeitbasierter Index:** `idx_price_history_watch_time` fuer schnelle Abfragen nach Zeitraum

**Pruefungssatz:** "Wir haben 7 Tabellen in 3. Normalform: users, watched_products und price_history bilden den Kern der Preisueberwachung. price_alerts trackt den Alert-Lifecycle von PENDING bis SENT. deal_filters und deal_reports ermoeglichen personalisierte Deal-Suchen. collected_deals ist unser Rohdaten-Archiv fuer historische Analyse."

---

## 4. ETL-Schritte — Von der API bis zum Dashboard

### E — Extraction (Daten holen)

**Was:** Keepa REST API → HTTP GET mit API-Key
**Wo im Code:** `src/services/keepa_api.py:455` → `query_product()`
**Details:**
- Keepa liefert Preise als **CSV-Arrays in Cent** (nicht Euro!)
- 4 Preistypen: Amazon, New, Used, Warehouse Deal
- Token-Bucket Rate Limiting: Keepa gibt verbleibende Tokens in jeder Response zurueck
- Deal-API fuer Batch-Abfragen mit Kategorie-Filter (Browse Node 340843031 = Tastaturen)

```
Keepa API Response → CSV-Arrays → Cent-Werte → [0, 2999, 1, 3499, 2, 2899, ...]
                                                  ^time  ^price ^time ^price
```

### T — Transformation (Daten aufbereiten)

**Was:** Rohdaten normalisieren, filtern, scoren
**Wo im Code:** `src/agents/deal_finder.py:299` → `_normalize_deal()`
**Details:**
- **Cent → Euro:** Division durch 100
- **Discount berechnen:** `(1 - current_price / list_price) * 100`
- **Keyboard-Filter:** Title-Keywords (tastatur, keyboard, mechanical...) + Brand-Whitelist (Logitech, Cherry, Corsair...)
- **Deal-Scoring:** `_score_deal()` bewertet Discount, Rating, Reviews → gewichteter Score
- **Layout-Erkennung:** QWERTZ-spezifisch (DE/AT/CH) durch Seed-ASINs

```python
# _normalize_deal() — Kernlogik
discount_percent = round((1 - current_price / list_price) * 100, 1)
# Ergebnis: einheitliches Dict mit asin, title, current_price, discount_percent, rating, ...
```

### L — Load/Speicherung (Daten persistieren)

**Was:** Drei parallele Ziele (Triple-Write)
**Wo im Code:** `src/scheduler.py:278` → `run_price_check()`

| Ziel | Technologie | Warum |
|------|-------------|-------|
| **PostgreSQL** | `update_watch_price()`, `save_collected_deals_batch()` | ACID-Konsistenz, Source of Truth, relationale Abfragen |
| **Elasticsearch** | `es_service.index_price_update()` | Volltextsuche, schnelle Aggregationen, Kibana-Anbindung |
| **Kafka** | `price_producer.send_price_update()` | Event-Stream, Entkopplung, 7-Tage-Puffer, Replay |

**Reihenfolge:** DB zuerst (Source of Truth), dann Kafka (Puffer), dann ES (Suchindex). Bei ES-Ausfall keine Datenverluste.

### A — Analyse (Daten auswerten)

**Was:** Dashboards, Scoring, Reports
**Wo:**
- **Kibana Dashboards:** Preis-Trends, Deal-Statistiken, Discount-Verteilungen (`localhost:5601`)
- **Deal-Scoring:** `_score_deal()` gewichtet Discount (40%), Rating (30%), Reviews (20%), Sales Rank (10%)
- **Daily Deal Reports:** `run_daily_deal_reports()` — personalisierte Email-Reports basierend auf DealFilter
- **Alert-System:** Preis unter Zielpreis → PriceAlert → Dispatch ueber Email/Telegram/Discord

**Pruefungssatz:** "Unsere Pipeline hat 4 Schritte: Extraction aus der Keepa API mit Token-basiertem Rate Limiting, Transformation mit Normalisierung und Keyboard-spezifischem Filtering, paralleles Loading in PostgreSQL, Kafka und Elasticsearch, und Analyse ueber Kibana-Dashboards und automatische Deal-Reports."

---

## Schnell-Referenz: Die 4 Schwerpunkte in je 1 Satz

| Schwerpunkt | Der eine Satz |
|-------------|---------------|
| **Containers** | 8 Docker-Services mit depends_on-Kette, 4 Named Volumes, Scheduler als eigener Container fuer Separation of Concerns |
| **Automatisierung** | Custom async Scheduler mit `asyncio.sleep(6h)` — reicht fuer unsere lineare Pipeline, Airflow waere Overkill |
| **Datenbank** | 7 Tabellen in 3NF: users/watches/history/alerts fuer Preisueberwachung, filters/reports fuer Personalisierung, collected_deals als Rohdaten-Archiv |
| **ETL** | Extract (Keepa API, Cent-CSV), Transform (_normalize_deal, Keyboard-Filter, Scoring), Load (PG+Kafka+ES Triple-Write), Analyse (Kibana+Reports) |

---

*Erstellt am 2026-02-24 — Ergaenzt KILLFRAGEN_DRILL.md mit den 4 Prof-Schwerpunkten.*
*Quellen: docker-compose.yml, src/services/database.py, src/scheduler.py, src/agents/deal_finder.py*
