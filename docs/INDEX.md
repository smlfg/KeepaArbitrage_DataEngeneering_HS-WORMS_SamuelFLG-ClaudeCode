# Keeper System — Dokumentations-Index

## Core Technology Docs

| Dokument | Technologie | Was du lernst |
|----------|-------------|---------------|
| [KEEPA_API.md](KEEPA_API.md) | Keepa API | Endpoints, CSV-Format, Token Bucket, Domain-IDs, Preis-Parsing |
| [KEEPA_API_LEARNINGS.md](KEEPA_API_LEARNINGS.md) | Keepa API Praxis | Was funktioniert, was nicht, Fact-Check, Batch-Optimierung, Lessons Learned |
| [ELASTICSEARCH.md](ELASTICSEARCH.md) | Elasticsearch 8.11 | Indices, Mappings, Queries, Aggregationen |
| [KIBANA.md](KIBANA.md) | Kibana 8.11 | Discover, KQL, Dashboards, Dev Tools, Saved Searches |
| [DOCKER.md](DOCKER.md) | Docker Compose | Alle 8 Services, Ports, Volumes, Startup-Reihenfolge |
| [KAFKA.md](KAFKA.md) | Apache Kafka | Topics, Producer/Consumer, Message-Formate, Consumer Groups |
| [POSTGRESQL.md](POSTGRESQL.md) | PostgreSQL 15 | Schema, Tabellen, SQLAlchemy Models, nuetzliche Queries |
| [FASTAPI.md](FASTAPI.md) | FastAPI | REST-Endpoints, Request/Response Models, Swagger UI |

## Pipeline & Architektur

| Dokument | Inhalt |
|----------|--------|
| [PIPELINE_FLOW.md](PIPELINE_FLOW.md) | **Schritt-fuer-Schritt:** Wie ein Deal von Keepa bis ES fliesst (mit Code-Referenzen) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architektur-Entscheidungen, Datenfluss-Diagramme |
| [OVER_ENGINEERING_AUDIT.md](OVER_ENGINEERING_AUDIT.md) | Ehrliche Selbstreflexion: Was over-engineered ist, was nicht, und warum das okay ist |
| [project-deep-dive.md](project-deep-dive.md) | Technischer Deep-Dive |

## Lernen & Vorbereitung

| Dokument | Inhalt |
|----------|--------|
| [PRUEFUNGSVORBEREITUNG.md](PRUEFUNGSVORBEREITUNG.md) | Data Engineering Pruefungsvorbereitung |
| [../FOR_SMLFLG.md](../FOR_SMLFLG.md) | Persoenliches Lern-Dokument |
| [../ExplanationForSamuel.md](../ExplanationForSamuel.md) | Projekt-Erklaerung |

---

## Quick Reference: Was laeuft wo?

```
Dein Browser
    │
    ├── localhost:8000/docs  → FastAPI Swagger UI     [FASTAPI.md]
    ├── localhost:5601       → Kibana Dashboards       [ELASTICSEARCH.md]
    └── localhost:9200       → ES REST API             [ELASTICSEARCH.md]

Docker Container
    ├── app (FastAPI)        → REST API Server          [FASTAPI.md, DOCKER.md]
    ├── scheduler            → Preis-Check + Deals      [KEEPA_API.md, DOCKER.md]
    ├── db (PostgreSQL)      → Source of Truth           [POSTGRESQL.md]
    ├── kafka                → Event Streaming           [KAFKA.md]
    ├── zookeeper            → Kafka Coordination        [KAFKA.md]
    ├── elasticsearch        → Such-Engine               [ELASTICSEARCH.md]
    ├── kibana               → Visualisierung            [ELASTICSEARCH.md]
    └── kafka-connect        → Optional Bridge           [KAFKA.md]
```

## Datenfluss (End-to-End)

```
Keepa API ──→ Scheduler ──→ Kafka Producer ──→ Kafka Topics
  [KEEPA]       [DOCKER]      [KAFKA]            [KAFKA]
                    │                                │
                    ├──→ Elasticsearch ←──── Kafka Consumer
                    │     [ELASTICSEARCH]     [KAFKA]
                    │
                    └──→ PostgreSQL ←──────── Kafka Consumer
                          [POSTGRESQL]         [KAFKA]
                                │
                    FastAPI ←───┘
                    [FASTAPI]
```

---

## Archiv

Historische / nicht pruefungsrelevante Docs und Scripts wurden in Archive-Ordner verschoben,
um den ersten Eindruck sauber zu halten. Nichts geloescht — alles via `git` reversibel.

| Ordner | Inhalt |
|--------|--------|
| `docs/archive/` | ClaudeChromeExtension.md, CODE_REVIEW.md, todoDB.md, SWARM_REPORT_PRUEFUNG.md |
| `scripts/archive/` | Einmal-Scripts fuer Seed-Daten (collect_1000_keyboards, scrape_amazon_asins, discover_*) |
