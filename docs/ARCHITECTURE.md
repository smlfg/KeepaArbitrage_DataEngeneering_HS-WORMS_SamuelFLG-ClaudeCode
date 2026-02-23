# Architektur-Entscheidungen — Keeper System

## 1. Architekturdiagramm

```
                          ┌──────────────────────────┐
                          │      KEEPA REST API       │
                          │  (Amazon Preis-Daten)     │
                          └────────────┬─────────────┘
                                       │ HTTP GET /product
                                       │ (Token-Bucket Rate Limiting)
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         KEEPER SYSTEM                                │
│                                                                      │
│  ┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐  │
│  │  SCHEDULER   │────▶│  KEEPA API CLIENT │────▶│  KAFKA PRODUCER  │  │
│  │ (APScheduler)│     │  (Token Bucket)   │     │  (price-updates) │  │
│  │  6h Intervall│     │  (Parallel Fetch) │     │  (deal-updates)  │  │
│  └─────────────┘     └──────────────────┘     └────────┬─────────┘  │
│                                                         │            │
│                              ┌──────────────────────────┤            │
│                              │                          │            │
│                              ▼                          ▼            │
│                   ┌──────────────────┐      ┌──────────────────┐    │
│                   │  KAFKA CONSUMER   │      │  KAFKA CONSUMER   │    │
│                   │  (Group: prices)  │      │  (Group: deals)   │    │
│                   └────────┬─────────┘      └────────┬─────────┘    │
│                            │                          │              │
│                            ▼                          ▼              │
│                   ┌──────────────────┐      ┌──────────────────┐    │
│                   │   POSTGRESQL     │      │  ELASTICSEARCH   │    │
│                   │   (Port 5432)    │      │   (Port 9200)    │    │
│                   │                  │      │                  │    │
│                   │ - watches        │      │ - keeper-prices  │    │
│                   │ - price_history  │      │ - keeper-deals   │    │
│                   │ - collected_deals│      │                  │    │
│                   └──────────────────┘      └────────┬─────────┘    │
│                                                       │              │
│                                              ┌────────▼─────────┐    │
│                                              │     KIBANA        │    │
│                                              │   (Port 5601)     │    │
│                                              │  Dashboards +     │    │
│                                              │  Visualisierung   │    │
│                                              └──────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                     AGENT SYSTEM                                 │ │
│  │  ┌───────────────┐ ┌───────────────┐ ┌────────────────────┐    │ │
│  │  │Price Monitor  │ │ Deal Finder   │ │ Alert Dispatcher   │    │ │
│  │  │ - Schwellwert │ │ - Scoring     │ │ - Email (SMTP)     │    │ │
│  │  │ - Vergleich   │ │ - Filterung   │ │ - Telegram Bot     │    │ │
│  │  │ - Alerts      │ │ - Ranking     │ │ - Discord Webhook  │    │ │
│  │  └───────────────┘ └───────────────┘ └────────────────────┘    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    FASTAPI REST API (Port 8000)                  │ │
│  │  /health  /api/v1/watches  /api/v1/deals  /api/v1/price        │ │
│  │  /docs (Swagger UI)  /api/v1/tokens  /api/v1/rate-limit        │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. Technologie-Entscheidungen mit Begründung

### Warum PostgreSQL als primäre Datenbank?

| Kriterium | Entscheidung | Begründung |
|-----------|-------------|------------|
| **ACID-Transaktionen** | PostgreSQL | Preis-History und Watches brauchen Konsistenz — kein Datenverlust bei Concurrent Writes |
| **Relationale Verknüpfungen** | PostgreSQL | Watches → PriceHistory → CollectedDeals haben klare Beziehungen |
| **Async-Support** | asyncpg | FastAPI ist async — synchrone DB-Calls wären ein Bottleneck |
| **Alternative erwogen** | MongoDB | Abgelehnt: Schema-Flexibilität nicht nötig, relationale Queries aber schon |

### Warum Apache Kafka für Event Streaming?

| Kriterium | Entscheidung | Begründung |
|-----------|-------------|------------|
| **Entkopplung** | Kafka | Producer (Scheduler) und Consumer (DB-Writer) sind unabhängig — Ausfälle propagieren nicht |
| **Durability** | Log-basiert | Nachrichten bleiben 7 Tage erhalten (KAFKA_LOG_RETENTION_HOURS=168) — Replay möglich |
| **Consumer Groups** | 2 Gruppen | `prices` und `deals` können unabhängig skaliert werden |
| **Alternative erwogen** | RabbitMQ | Abgelehnt: Kafka passt besser zu Log-basierten Pipelines; VL-Anforderung |

### Warum Elasticsearch + Kibana für Analytics?

| Kriterium | Entscheidung | Begründung |
|-----------|-------------|------------|
| **Volltext-Suche** | Elasticsearch | Deals nach Titel, Kategorie, Markt durchsuchbar mit Relevanz-Scoring |
| **Aggregationen** | Elasticsearch | Durchschnittspreise, Top-Deals, Preis-Trends ohne komplexe SQL-Queries |
| **Visualisierung** | Kibana | Out-of-the-box Dashboards für Preis-Trends und Deal-Statistiken |
| **Dual-Write** | PG + ES | PostgreSQL für ACID-Garantien, ES für schnelle Suche — bewusster Trade-off |
| **Alternative erwogen** | Nur PostgreSQL | Abgelehnt: PG-Volltext-Suche zu limitiert für Aggregationen und Dashboards |

### Warum FastAPI?

| Kriterium | Entscheidung | Begründung |
|-----------|-------------|------------|
| **Performance** | Async/ASGI | Nicht-blockierende I/O für parallele Keepa API Calls |
| **Dokumentation** | Auto-Swagger | `/docs` Endpoint automatisch generiert — kein manueller Aufwand |
| **Typisierung** | Pydantic | Request/Response Validation built-in |

## 3. Trade-offs — Was wir bewusst nicht gemacht haben

| Was wir **nicht** gemacht haben | Warum |
|--------------------------------|-------|
| **Kein Redis-Cache** | Keepa API hat eigenes Rate Limiting (Token Bucket im Code). Cache würde Komplexität erhöhen ohne proportionalen Nutzen für unser Intervall (6h). |
| **Kein Multi-Broker Kafka Cluster** | Single-Broker reicht für unser Datenvolumen (~100 Nachrichten/Zyklus). Cluster-Skalierung ist vorbereitet (Compose-Konfiguration erweiterbar). |
| **Kein Elasticsearch-Cluster** | Single-Node für Development/Demo. Production würde 3+ Nodes mit Replicas brauchen — `discovery.type=single-node` bewusst gesetzt. |
| **Keine Echtzeit-Alerts** | Periodisches Monitoring (6h-Intervall) statt WebSocket-Push. Begründung: Keepa API hat Token-Limits, Echtzeit wäre zu teuer. |
| **Kein ML-basiertes Scoring** | Deal-Scoring ist regelbasiert (Discount × Rating × SalesRank). ML-Modell wäre Over-Engineering für den Use Case. |
| **Keine Frontend-Anwendung** | Kibana + Swagger UI decken die Visualisierung ab. Eigenes Frontend wäre Scope Creep. |

## 4. Fünf typische Prüferfragen mit Antworten

### F1: „Warum zwei Speichersysteme (PostgreSQL + Elasticsearch)?"

> **Antwort:** Jedes System hat eine klare Aufgabe. PostgreSQL garantiert ACID-Transaktionen für die Preis-History — wenn ein Preis gespeichert wird, ist er da. Elasticsearch ist für schnelle Suche und Aggregationen optimiert — „Zeige mir alle Deals unter 50€ mit Rating > 4.0 sortiert nach Discount" ist eine Zeile in ES, aber ein komplexer Join in SQL.
>
> Der Trade-off ist Dual-Write-Komplexität: Wir schreiben in beide Systeme. Bei Ausfall von ES verlieren wir keine Daten (PG ist Source of Truth), nur die Suche ist temporär eingeschränkt.

### F2: „Was passiert, wenn Kafka ausfällt?"

> **Antwort:** Kafka ist mit `log.retention.hours=168` konfiguriert — Nachrichten bleiben 7 Tage. Bei kurzem Ausfall holt der Consumer beim Neustart automatisch nach (Offset-basiert). Bei längerem Ausfall schreibt der Scheduler weiterhin — Nachrichten akkumulieren im Log. Das ist der Vorteil von Kafkas Architektur gegenüber klassischen Message Queues: kein Message Loss.
>
> Im Worst Case (Kafka komplett weg): Der Scheduler loggt Fehler, die Daten gehen verloren. Für Production würde man Replication Factor > 1 setzen.

### F3: „Wie stellt ihr sicher, dass die Keepa API nicht überlastet wird?"

> **Antwort:** Wir haben einen Token-Bucket-Algorithmus implementiert (`src/services/keepa_api.py`). Keepa gibt mit jeder Response die verbleibenden Tokens zurück. Unser Client wartet automatisch, wenn Tokens unter den Schwellwert fallen. Zusätzlich limitiert der Scheduler die Batch-Größe (`DEAL_SCAN_BATCH_SIZE`) und das Intervall (`DEAL_SCAN_INTERVAL_SECONDS`).
>
> Parallele Requests werden mit `asyncio.gather()` mit Semaphore begrenzt, um nicht mehr als N gleichzeitige Connections zu öffnen.

### F4: „Warum kein Echtzeit-System?"

> **Antwort:** Bewusste Entscheidung. Amazon-Preise ändern sich nicht sekundengenau — periodisches Monitoring (alle 6h) reicht für den Use Case „Deal-Finder". Echtzeit würde die Keepa API Token-Kosten um Faktor 100+ erhöhen, ohne proportionalen Mehrwert. Unser Fokus ist **periodisches Monitoring + API-Analytics**, nicht Echtzeit-Trading.

### F5: „Wie würdet ihr das System für 10× mehr Produkte skalieren?"

> **Antwort:** Drei Hebel:
> 1. **Kafka**: Mehr Partitions pro Topic + mehr Consumer in der Consumer Group → horizontale Skalierung
> 2. **Elasticsearch**: Von Single-Node auf 3-Node-Cluster mit Sharding → Suchperformance skaliert linear
> 3. **Scheduler**: Batch-Größe erhöhen + kürzere Intervalle + mehrere Scheduler-Instanzen mit Offset-Koordination (bereits implementiert via `start_offset`)
>
> PostgreSQL wäre der Bottleneck — hier würde man Read Replicas oder Partitionierung der PriceHistory-Tabelle nach Datum einsetzen.

## 5. 10-Minuten-Storyline für die Präsentation

### Minute 0–1: Problem und Motivation (30 Sekunden)
> „Wir haben ein System gebaut, das automatisch Amazon-Preise überwacht und die besten Deals findet. Die Idee: Statt manuell Preise zu vergleichen, übernimmt unsere Pipeline das — periodisch, automatisiert, mit Notifications."

### Minute 1–3: Architektur-Überblick (2 Minuten)
> Diagramm zeigen. Drei Schichten erklären:
> 1. **Ingestion**: Keepa API → Scheduler holt alle 6h Preise
> 2. **Processing**: Kafka entkoppelt Producer/Consumer + Deal-Scoring normalisiert und bewertet
> 3. **Storage**: PostgreSQL für Konsistenz, Elasticsearch für Suche

### Minute 3–5: Live-Demo (2 Minuten)
> 1. `docker-compose up -d` zeigen (alles startet)
> 2. `curl /health` → System ist gesund
> 3. `POST /api/v1/watches` → Produkt hinzufügen
> 4. `POST /api/v1/deals/search` → Deals finden
> 5. Kibana Dashboard zeigen → Preis-Trends visualisiert

### Minute 5–7: Technologie-Entscheidungen (2 Minuten)
> Die drei wichtigsten Entscheidungen:
> 1. **Dual Storage** (PG + ES): Warum nicht nur eins?
> 2. **Kafka statt direktem DB-Write**: Entkopplung und Durability
> 3. **Periodisch statt Echtzeit**: Kosten vs. Nutzen

### Minute 7–9: Code-Highlights (2 Minuten)
> 1. Token-Bucket Rate Limiting (`keepa_api.py`) — elegante API-Schonung
> 2. Deal-Normalisierung (`deal_finder.py:_normalize_deal`) — camelCase → snake_case
> 3. Kafka Consumer Groups — unabhängige Verarbeitung

### Minute 9–10: Grenzen und nächste Schritte (1 Minute)
> **Grenzen:**
> - Single-Node ES (kein HA)
> - Keine Frontend-UI (nur Swagger + Kibana)
> - Keepa API Token-Kosten limitieren Skalierung
>
> **Nächste Schritte:**
> - Elasticsearch-Cluster für Production
> - ML-basiertes Deal-Scoring
> - Frontend Dashboard mit React
