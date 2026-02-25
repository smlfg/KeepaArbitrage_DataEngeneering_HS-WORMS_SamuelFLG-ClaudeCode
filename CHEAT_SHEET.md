# KEEPER — Pruefungs-Cheat-Sheet

## Datenfluss (Big Picture)
```
Keepa API → KeepaClient (Token Bucket 20/min, asyncio.Lock)
  → Score & Filter → Triple-Write:
  PostgreSQL (Wahrheit) + Kafka (Events) + Elasticsearch (Suche)
  → AlertDispatcher → Email / Telegram / Discord
```

## Die 3 Prof-Fragen

**1. Warum Kafka UND PostgreSQL?**
Kafka = Event-Streaming (lose Kopplung, Replay, Consumer Groups). PostgreSQL = Source of Truth mit ACID. Kafka Consumers schreiben Events in die DB — Consumer Pattern.

**2. Wie funktioniert Rate Limiting?**
Token Bucket: 20 Tokens/min, jeder API-Call kostet Tokens. Leer → async warten bis Refill. asyncio.Lock gegen Race Conditions bei parallelen Calls.

**3. Was wenn Kafka ausfaellt?**
Graceful Degradation: Warning loggen, weiterlaufen. Preise landen trotzdem in PostgreSQL. Neue Events normal wenn Kafka zurueck. Kein Retry-Buffer — bewusste Design-Entscheidung fuer Einfachheit.

## Technologie-Entscheidungen

| Tech | Warum | Nicht... |
|------|-------|----------|
| APScheduler | 2 Jobs, 1 Container, in-process, null Overhead | Airflow (eigene Infra, Webserver, Celery) |
| Token Bucket | Keepa gibt remaining zurueck, async-faehig | Redis (unnoetig fuer 1 Instanz) |
| PG + ES | ACID + Volltextsuche + Facetten | Nur PG (Suche langsam bei Volltextsuche) |
| Kafka | Events, Replay, lose Kopplung, Consumer Groups | Direct DB Writes (eng gekoppelt) |
| FastAPI | Async, auto API-Docs, schnell | Flask (sync, kein auto-Schema) |

## Docker Services (7 + 1 Setup)

| Service | Port | Image/Funktion |
|---------|------|----------------|
| app | 8000 | FastAPI REST-API + Swagger UI |
| db | 5432 | PostgreSQL 15 (Source of Truth) |
| kafka | 9092 | Confluent Kafka 7.5 (Events) |
| zookeeper | 2181 | Kafka-Koordination |
| elasticsearch | 9200 | ES 8.11 (Suche + Deals-Index) |
| kibana | 5601 | Dashboards + Discover |
| scheduler | — | APScheduler (Price Check + Deal Collector) |

## Demo-Befehle (Top 5)
```bash
docker-compose up -d                                    # Alles starten
curl localhost:8000/health                               # Health-Check
curl localhost:8000/price-check/B09V3KXJPB               # Einzelprodukt
curl localhost:8000/watches                              # Aktive Watches
curl 'localhost:9200/keeper-deals/_search?pretty&size=3'  # ES Deals
```

## Fehlerverhalten (Graceful Degradation)

| Fehler | Reaktion |
|--------|----------|
| Keepa down / Rate Limit | 60s warten, 1x Retry, dann Domain skip |
| Kafka down | Warning, weiter mit PG + ES |
| ES down | Log + Return, kein Crash |
| Preis = -1 | Skip (nicht verfuegbar) |
| Seed-Datei fehlt | Fallback: TXT → Env → JSON → 3 Defaults |

## Scoring-Formel (Deal Discovery)
`Score = 50% Rabatt + 35% Bewertung + 10% Sales-Rank + 5% Preis`
Mindest-Rabatt: 10%. Spam-Filter: zu billig, zu hoher Rabatt, Dropshipper-Keywords.
