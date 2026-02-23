# Keeper System — Amazon Preis-Monitoring & Deal-Finder

## Was ist das?

Ein System das Amazon-Preise überwacht und automatisch Deals findet. Du sagst "Benachrichtige mich wenn dieses Produkt unter 50€ fällt" — das System checkt regelmäßig und schickt dir eine E-Mail, Telegram- oder Discord-Nachricht wenn's soweit ist.

## Die 3 Hauptfunktionen

### 1. Price Watches — "Sag mir Bescheid wenn der Preis stimmt"
- User erstellt einen Watch: ASIN + Zielpreis
- Scheduler prüft alle 6h die Preise via Keepa API
- Fällt der Preis unter den Zielpreis → Alert wird ausgelöst

### 2. Deal Discovery — "Zeig mir die besten Schnäppchen"
- Nimmt eine Liste von Seed-ASINs (aus CSV-Datei oder hardcoded)
- Fragt Keepa nach aktuellen Preisen für jede ASIN
- Bewertet Deals nach Score (50% Rabatt, 35% Bewertung, 10% Sales-Rank, 5% Preis)
- Filtert Spam raus (zu billig, zu hoher Rabatt, Dropshipper-Keywords)

### 3. Deal Reports — "Schick mir täglich einen Bericht"
- User hat gespeicherte Filter (Kategorie, Min/Max-Rabatt, Preisrange)
- Einmal pro Tag generiert das System einen HTML-Report
- Verschickt per E-Mail an den jeweiligen User

## Wie hängt das zusammen? (Datenfluss)

```
Keepa API
    │
    ▼
┌─────────────────┐
│  keepa_api.py   │  ← Token Bucket (20 Tokens/min), Rate Limiting
│  (KeepaClient)  │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
 Watches    Deals
    │         │
    ▼         ▼
┌────────┐ ┌──────────┐
│Scheduler│ │DealFinder│  ← Seed-ASINs → Produkt-Abfrage → Score → Filter
└──┬─────┘ └────┬─────┘
   │             │
   ▼             ▼
┌──────────────────────────────────────┐
│         Triple-Write Pipeline        │
│  PostgreSQL  │  Kafka  │  Elastic    │
│  (Wahrheit)  │ (Event) │  (Suche)    │
└──────────────────────────────────────┘
   │
   ▼
┌──────────────────┐
│ AlertDispatcher   │  ← Duplikat-Check, Rate-Limit, Retry-Logic
│ Email/TG/Discord  │
└──────────────────┘
```

## Die Dateien und was sie tun

| Datei | Aufgabe |
|-------|---------|
| `src/config.py` | Settings aus `.env` laden (Pydantic) |
| `src/scheduler.py` | **Das Herzstück** — Loop alle 6h: Preise checken, Deals sammeln, Alerts verschicken |
| `src/services/keepa_api.py` | Keepa API Client mit Token Bucket (Rate Limiting) |
| `src/services/database.py` | PostgreSQL: Watches, Alerts, Deals speichern |
| `src/services/kafka_producer.py` | Events an Kafka senden (Preis-Updates, Deal-Updates) |
| `src/services/kafka_consumer.py` | Events aus Kafka lesen |
| `src/services/elasticsearch_service.py` | Deals durchsuchbar machen (Volltextsuche, Aggregationen) |
| `src/services/notification.py` | E-Mail, Telegram, Discord senden |
| `src/agents/deal_finder.py` | Deal-Suche, Scoring, Spam-Filter, Report-Generierung |
| `src/agents/price_monitor.py` | Preis-Abfragen mit Volatilitäts-basiertem Intervall |
| `src/agents/alert_dispatcher.py` | Alert-Versand mit Duplikat-Check, Rate-Limit, Retries |
| `src/api/main.py` | FastAPI REST-API (CRUD für Watches, Deal-Suche, Health-Check) |

## Der Scheduler im Detail (das ist wo die Magie passiert)

Wenn `scheduler.py` startet, passieren **zwei Dinge parallel**:

### Loop A — Price Check (alle 6h):
1. Alle aktiven Watches aus PostgreSQL holen
2. Für jeden Watch → Keepa API fragen → neuen Preis bekommen
3. Preis in PostgreSQL updaten
4. Event an Kafka senden
5. Preis in Elasticsearch indexieren
6. Wenn Preis ≤ Zielpreis → Alert erstellen → Alert verschicken

### Loop B — Deal Collector (konfigurierbar, default 1h):
1. Seed-ASINs aus CSV/Config laden
2. Batch von ASINs bei Keepa abfragen
3. Deals scoren und filtern
4. In alle 3 Stores schreiben: PostgreSQL + Kafka + Elasticsearch

Jeden 4. Zyklus (= ~1x pro Tag) werden zusätzlich **Deal Reports** generiert und per E-Mail verschickt.

## Warum diese Technologien?

- **PostgreSQL** — Source of Truth. Watches, Alerts, User-Daten. Relationale Daten, ACID-Garantien.
- **Kafka** — Event Streaming. Jede Preis-Änderung ist ein Event. Kafka Connect synct automatisch nach Elasticsearch.
- **Elasticsearch + Kibana** — Such-Engine für Deals. Volltextsuche, Facetten, Dashboards. Kibana auf Port 5601.
- **Keepa API** — Die einzige zuverlässige Quelle für Amazon-Preishistorien. Hat ein Token-System (20 Tokens/Minute), daher der Token Bucket.
- **FastAPI** — REST-API für Frontend/Integrationen. Async, schnell, automatische API-Docs.

## Was gerade der Stand ist

Das System ist funktional aber noch im Dev-Modus:
- Docker Compose orchestriert alles (PG, Kafka, ES, Kibana, App, Scheduler)
- Scheduler läuft mit 1h Scan-Intervall und 10er Batches
- CORS ist jetzt auf localhost eingeschränkt (gefixt am 19.02.2026)
- Redis wurde entfernt weil ungenutzt (gefixt am 19.02.2026)
- Token-Verbrauch wird jetzt kumulativ geloggt (gefixt am 19.02.2026)
- Kafka Consumer aktiviert — liest jetzt price-updates und deal-updates Topics (19.02.2026)
- Token Bucket mit asyncio.Lock abgesichert gegen Race Conditions (19.02.2026)
- Price Checks parallelisiert mit Semaphore(5) statt sequentiell (19.02.2026)
- Legacy Keepa Client entfernt — nur noch ein Singleton (19.02.2026)

---

## Demo-Runbook (Prüfungsvorbereitung)

### Schritt 1: System starten

```bash
cd KeepaProjectsforDataEngeneering3BranchesMerge

# .env Datei konfigurieren (KEEPA_API_KEY muss gesetzt sein!)
cp .env.example .env
nano .env  # KEEPA_API_KEY eintragen

# Alles starten
docker-compose up -d
```

### Schritt 2: Prüfen ob alle Services laufen

```bash
# Container-Status
docker-compose ps

# Health-Check der App
curl http://localhost:8000/health

# Kafka Topics anzeigen
docker-compose exec kafka kafka-topics --list --bootstrap-server localhost:9092
# Erwartete Topics: price-updates, deal-updates

# Elasticsearch prüfen
curl http://localhost:9200/_cat/indices?v
# Erwartete Indices: keeper-prices, keeper-deals

# PostgreSQL prüfen
docker-compose exec db psql -U postgres -d keeper -c "\dt"
# Erwartete Tabellen: users, watched_products, price_alerts, deal_filters, collected_deals
```

### Schritt 3: API-Endpoints demonstrieren

```bash
# Swagger UI öffnen
# Browser: http://localhost:8000/docs

# Health-Check
curl http://localhost:8000/health

# Token-Status (zeigt Keepa API Rate Limiting)
curl http://localhost:8000/tokens

# Einzelnes Produkt abfragen
curl http://localhost:8000/price-check/B09V3KXJPB

# Watch erstellen (Preis-Alarm)
curl -X POST http://localhost:8000/watches \
  -H "Content-Type: application/json" \
  -d '{"asin": "B09V3KXJPB", "target_price": 50.00}'

# Aktive Watches anzeigen
curl http://localhost:8000/watches

# Deal-Suche starten
curl http://localhost:8000/deals/search

# Beste Deals anzeigen
curl http://localhost:8000/deals/best
```

### Schritt 4: Data Pipeline in Aktion zeigen

```bash
# Kafka Consumer Logs — zeigt eingehende Events
docker-compose logs -f app 2>&1 | grep -i "consumer\|kafka"

# Elasticsearch Suche — zeigt indexierte Deals
curl 'http://localhost:9200/keeper-deals/_search?pretty&size=5'

# Kibana Dashboard öffnen
# Browser: http://localhost:5601
# → Discover → keeper-deals* oder keeper-prices*

# PostgreSQL Deals ansehen
docker-compose exec db psql -U postgres -d keeper \
  -c "SELECT asin, title, current_price, discount_percent FROM collected_deals ORDER BY created_at DESC LIMIT 5;"
```

### Schritt 5: Architektur-Erklärung für Prof

**Drei Fragen die der Prof stellen könnte:**

1. **"Warum Kafka UND PostgreSQL?"**
   → Kafka ist für Event-Streaming (lose Kopplung, Replay-Fähigkeit, Consumer Groups für Skalierung). PostgreSQL ist die Source of Truth mit ACID-Garantien. Kafka Consumers schreiben die Events in die DB — das ist das Consumer Pattern.

2. **"Wie funktioniert das Rate Limiting?"**
   → Token Bucket Algorithmus: 20 Tokens/Minute, jeder API-Call kostet Tokens. Wenn leer → async warten bis Refill. Mit asyncio.Lock gegen Race Conditions bei parallelen Calls abgesichert.

3. **"Was passiert wenn Kafka ausfällt?"**
   → Graceful Degradation: Scheduler loggt eine Warnung und läuft weiter. Preise werden trotzdem in PostgreSQL geschrieben. Wenn Kafka wieder da ist, werden neue Events normal gesendet. Alte Events gehen verloren (kein Retry-Buffer implementiert — bewusste Design-Entscheidung für Einfachheit).

### Schritt 6: System stoppen

```bash
docker-compose down        # Stoppen, Daten bleiben (Volumes)
docker-compose down -v     # Stoppen + Volumes löschen (Clean Start)
```
