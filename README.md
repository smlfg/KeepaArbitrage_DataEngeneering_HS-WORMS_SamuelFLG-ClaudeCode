# Keeper System - Amazon Price Monitoring & Deal Finder

**Data Engineering Project** — End-to-end Data Pipeline mit Kafka + Elasticsearch

> Periodisches Monitoring von Amazon-Preisen über die Keepa API, Verarbeitung via Apache Kafka, Speicherung in PostgreSQL + Elasticsearch, Visualisierung mit Kibana.

## Architektur

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         KEEPER SYSTEM                                        │
│                    Data Engineering Pipeline                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │  KEEPA API  │───▶│  KAFKA      │───▶│ CONSUMER    │───▶│ POSTGRESQL  │ │
│  │ (Scraping)  │    │  Producer   │    │             │    │  Database   │ │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘ │
│                           │                                                │
│                           │              ┌─────────────┐                   │
│                           └─────────────▶│ELASTICSEARCH│                   │
│                                          │  (Analytics)│                   │
│                                          └─────────────┘                   │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                        AGENT SYSTEM                                          │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                   │
│  │ Price Monitor │  │  Deal Finder  │  │Alert Dispatch│                   │
│  │    Agent      │  │    Agent      │  │    Agent     │                   │
│  └───────────────┘  └───────────────┘  └───────────────┘                   │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                        FASTAPI REST API                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Voraussetzungen

| Requirement | Version | Prüfen mit |
|-------------|---------|------------|
| Docker + Docker Compose | 20.10+ / v2+ | `docker --version && docker compose version` |
| Python | 3.11+ | `python3 --version` |
| Keepa API Key | — | [keepa.com](https://keepa.com/#!api) |

## Quickstart (3 Schritte)

```bash
# 1. Repository klonen
git clone https://github.com/smlfg/DataEngeneering_KEEPA_HSWORMS.git
cd DataEngeneering_KEEPA_HSWORMS

# 2. Environment konfigurieren
cp .env.example .env
# KEEPA_API_KEY eintragen:
nano .env

# 3. Alle Services starten
docker-compose up -d
```

### Verifizierung

```bash
# Health Check — sollte {"status": "healthy"} zurückgeben
curl -s http://localhost:8000/health | python3 -m json.tool

# Swagger UI — interaktive API-Dokumentation
# Öffne im Browser: http://localhost:8000/docs

# Kibana — Elasticsearch Visualisierung
# Öffne im Browser: http://localhost:5601

# Kafka Topics prüfen
docker-compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list

# PostgreSQL Verbindung prüfen
docker-compose exec db psql -U postgres -d keeper -c "SELECT 1;"

# Elasticsearch Cluster-Status
curl -s http://localhost:9200/_cluster/health | python3 -m json.tool
```

## Data Engineering Features

### Three Pillars Architecture

1. **Scraping Layer** — Keepa API für Amazon Preis- und Produktdaten
2. **Processing Layer** — Kafka Producers/Consumers + Deal-Scoring + Normalisierung
3. **Storage Layer** — PostgreSQL (persistent) + Elasticsearch (Analytics/Suche)

### Apache Kafka Integration

- Topic: `price-updates` — Preis-Änderungen aus dem Scheduler
- Topic: `deal-updates` — Verarbeitete Deal-Daten
- Producer: Sendet Daten aus dem Scheduler-Service
- Consumer: Verarbeitet und speichert in PostgreSQL + Elasticsearch
- Consumer Groups für horizontale Skalierbarkeit

### Elasticsearch Integration

- Index: `keeper-prices` — Preis-Historie durchsuchbar
- Index: `keeper-deals` — Deals für Analytics und Aggregationen
- Volltext-Suche mit Filtern (Preis, Rating, Discount)
- Aggregations für Statistiken (Durchschnittspreise, Top-Deals)
- Kibana Dashboard auf Port 5601

## Features

- **Preis-Überwachung**: Automatische Alerts bei Preis-Drops
- **Deal-Finder**: Tägliche personalisierte Deal-Reports mit Scoring
- **Multi-Channel Notifications**: Email (SMTP), Telegram, Discord
- **Analytics**: Volltext-Suche und Aggregations via Elasticsearch
- **EU Multi-Market**: Unterstützung für DE, UK, FR, IT, ES

## Services

| Service | Port | Beschreibung |
|---------|------|--------------|
| FastAPI | 8000 | REST API + Swagger UI (`/docs`) |
| PostgreSQL | 5432 | Primäre Datenbank (Watches, Deals, PriceHistory) |
| Kafka | 9092 | Event Streaming (price-updates, deal-updates) |
| Zookeeper | 2181 | Kafka Coordination |
| Elasticsearch | 9200 | Suchmaschine + Analytics |
| Kibana | 5601 | Visualisierung + Dashboards |
| Kafka Connect | 8083 | Elasticsearch Sink Connector |
| Scheduler | — | Background Jobs (Preis-Checks, Deal-Scans) |

## API Endpoints

| Methode | Endpoint | Beschreibung |
|---------|----------|--------------|
| GET | `/health` | Health Check aller Services |
| GET | `/api/v1/status` | System-Status und Metriken |
| GET | `/api/v1/watches` | Alle Preis-Watches abrufen |
| POST | `/api/v1/watches` | Neues Produkt überwachen (ASIN + Schwellenwert) |
| DELETE | `/api/v1/watches/{id}` | Watch löschen |
| POST | `/api/v1/price/check` | Einzel-Preischeck für ein Produkt |
| POST | `/api/v1/price/check-all` | Alle Watches prüfen (Batch) |
| POST | `/api/v1/deals/search` | Deals suchen mit Filtern |
| POST | `/api/v1/deals/es-search` | Elasticsearch-basierte Deal-Suche |
| GET | `/api/v1/deals/aggregations` | Deal-Statistiken und Aggregationen |
| POST | `/api/v1/deals/index` | Deals manuell in Elasticsearch indexieren |
| GET | `/api/v1/tokens` | Keepa API Token-Status |
| GET | `/api/v1/rate-limit` | Rate-Limit-Status |

## Projektstruktur

```
keeper-system/
├── prompts/              # Agent System Prompts
├── src/
│   ├── agents/           # Agent-Implementierungen
│   │   ├── price_monitor.py    # Preis-Überwachung
│   │   ├── deal_finder.py      # Deal-Scoring + Normalisierung
│   │   └── alert_dispatcher.py # Notification-Routing
│   ├── services/         # Business Logic
│   │   ├── keepa_api.py          # Keepa API Client + Token Bucket
│   │   ├── database.py           # SQLAlchemy Models + Queries
│   │   ├── notification.py       # Email/Telegram/Discord
│   │   ├── kafka_producer.py     # Kafka Producer
│   │   ├── kafka_consumer.py     # Kafka Consumer
│   │   └── elasticsearch_service.py  # ES Client + Indexing
│   ├── api/              # FastAPI REST Endpoints
│   │   └── main.py       # Alle Endpoints + Swagger
│   ├── config.py         # Pydantic Settings
│   └── scheduler.py      # APScheduler Background Jobs
├── scripts/              # Utility-Scripts (ASIN Discovery, Seed Apply)
├── tests/                # Unit Tests (pytest)
├── data/                 # Seed-Dateien + Testdaten
├── docker-compose.yml    # Alle Services orchestriert
├── Dockerfile            # Python App Container
├── requirements.txt      # Python Dependencies
├── .env.example          # Environment Template
└── .env                  # Lokale Konfiguration (nicht im Git)
```

## Environment Variablen

| Variable | Beschreibung | Required |
|----------|--------------|----------|
| `KEEPA_API_KEY` | Keepa API Key | Ja |
| `DEAL_SOURCE_MODE` | Deal-Modus (`product_only`) | Ja |
| `DEAL_SEED_ASINS` | Inline ASIN Seeds (kommasepariert) | Nein |
| `DEAL_SEED_FILE` | Pfad zur Seed-Datei | Nein |
| `DEAL_SCAN_INTERVAL_SECONDS` | Collector-Intervall in Sekunden | Nein |
| `DEAL_SCAN_BATCH_SIZE` | ASINs pro Collector-Zyklus | Nein |
| `DATABASE_URL` | PostgreSQL Connection String | Ja |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka Broker Adresse | Ja |
| `ELASTICSEARCH_URL` | Elasticsearch URL | Ja |
| `SMTP_HOST` | Email Server | Nein |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | Nein |
| `DISCORD_WEBHOOK` | Discord Webhook URL | Nein |

## Data Pipeline Flow

```
1. Scheduler ruft Keepa API auf (konfigurierbar, Default: alle 6h)
       │
       ▼
2. Preis-Daten werden normalisiert + validiert
       │
       ├──────────────────┬──────────────────┐
       ▼                  ▼                  ▼
3. Kafka Producer   Elasticsearch     PostgreSQL
   (price-updates)  (keeper-prices)   (PriceHistory)
       │                  │                  │
       ▼                  ▼                  ▼
4. Kafka Consumer ──▶ Database ──▶ Alerts (Email/Telegram/Discord)
```

## Automatic EU ASIN Discovery

Automatische Entdeckung von Produkt-ASINs über Keepa-Endpoints (`query`, `search category`, `bestsellers`):

```bash
# 1) ~1000 ASINs aus UK/FR/IT/ES entdecken
python scripts/discover_eu_qwertz_asins.py \
  --api-key "$KEEPA_API_KEY" \
  --markets UK,FR,IT,ES \
  --seed-limit 1000 \
  --output-txt data/seed_asins_eu_qwertz.txt

# 2) Seeds in .env übernehmen
python scripts/apply_seed_env.py \
  --seed-file data/seed_asins_eu_qwertz.txt \
  --env-file .env \
  --inline-limit 0
```

## Overnight Storage Run

```bash
# Pipeline-Services starten
docker-compose up -d db kafka elasticsearch scheduler

# Scheduler-Logs verfolgen
docker-compose logs -f scheduler

# PostgreSQL-Wachstum prüfen
docker-compose exec db psql -U postgres -d keeper -c "SELECT count(*) FROM collected_deals;"

# Elasticsearch-Wachstum prüfen
curl -s "http://localhost:9200/keeper-deals/_count" | python3 -m json.tool
```

## Testing

```bash
# Alle Tests ausführen
pytest

# Mit Coverage-Report
pytest --cov=src

# Nur Deal-Finder Tests
pytest tests/test_agents/test_deal_finder.py -v
```

## Lizenz

MIT
