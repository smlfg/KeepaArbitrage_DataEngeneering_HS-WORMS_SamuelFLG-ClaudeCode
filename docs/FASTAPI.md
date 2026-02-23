# FastAPI — Referenz fuer das Keeper System

## Was macht FastAPI hier?

FastAPI ist unser **REST-API-Layer** — die Schnittstelle zwischen der Aussenwelt und dem System. Ueber die API kann man:

- Produkte zur Ueberwachung hinzufuegen (Watches)
- Preise manuell pruefen
- Deals suchen (via Keepa oder Elasticsearch)
- Preis-Statistiken und -Historie abrufen
- System-Status und Token-Budget einsehen

**Version:** 2.0.0
**Port:** 8000
**Datei:** `src/api/main.py`

---

## Swagger UI

FastAPI generiert automatisch eine interaktive API-Dokumentation:

**URL:** http://localhost:8000/docs

Dort kannst du jeden Endpoint direkt im Browser testen.

---

## Startup (Lifespan)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Beim Start:
    await init_db()          # PostgreSQL Tabellen erstellen
    await es_service.connect()  # Elasticsearch verbinden

    yield  # App laeuft

    # Beim Shutdown:
    await es_service.close()
```

**Was passiert beim Start:**
1. Datenbank-Tabellen werden erstellt (falls nicht vorhanden)
2. Elasticsearch-Verbindung wird hergestellt + Indices erstellt
3. App ist bereit

---

## CORS Middleware

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5601", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Erlaubt Zugriffe von:
- `localhost:5601` — Kibana Dashboards
- `localhost:3000` — Frontend (falls vorhanden)

---

## Endpoint-Referenz

### Health & Status

#### `GET /health`

System-Health-Check. Zeigt Token-Status und Watch-Anzahl.

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "timestamp": "2026-02-20T12:00:00",
  "tokens_available": 150,
  "watches_count": 12
}
```

#### `GET /api/v1/status`

Detaillierter System-Status mit Token Bucket und Rate Limit Info.

```bash
curl http://localhost:8000/api/v1/status
```

```json
{
  "system": "healthy",
  "version": "2.0.0",
  "token_bucket": {
    "tokens_available": 150,
    "tokens_per_minute": 200,
    "last_refill": "2026-02-20T11:59:00",
    "total_tokens_consumed": 450
  },
  "rate_limit": {
    "tokens_available": 150,
    "refill_in_seconds": 45
  }
}
```

---

### Watches (Preis-Ueberwachung)

#### `GET /api/v1/watches?user_id=UUID`

Alle aktiven Watches eines Users auflisten.

```bash
curl "http://localhost:8000/api/v1/watches?user_id=550e8400-e29b-41d4-a716-446655440000"
```

```json
[
  {
    "id": "uuid...",
    "asin": "B07W6JN8V8",
    "target_price": 45.00,
    "current_price": 49.99,
    "status": "ACTIVE",
    "last_checked_at": "2026-02-20T06:00:00",
    "created_at": "2026-02-15T10:30:00"
  }
]
```

#### `POST /api/v1/watches?user_id=UUID`

Neue Watch erstellen. Fragt sofort den aktuellen Preis bei Keepa ab.

```bash
curl -X POST "http://localhost:8000/api/v1/watches?user_id=UUID" \
  -H "Content-Type: application/json" \
  -d '{"asin": "B07W6JN8V8", "target_price": 45.00}'
```

**Request Body:**
```json
{
  "asin": "B07W6JN8V8",     // Genau 10 Zeichen
  "target_price": 45.00      // EUR, muss > 0 sein
}
```

**Response:** Watch-Objekt mit aktuellem Preis (201 Created)

**Fehler:**
- 400 — Ungueltige ASIN oder target_price
- 500 — Keepa API Fehler

#### `DELETE /api/v1/watches/{watch_id}?user_id=UUID`

Watch loeschen (Soft Delete → Status wird INACTIVE).

```bash
curl -X DELETE "http://localhost:8000/api/v1/watches/uuid...?user_id=UUID"
```

---

### Preis-Check

#### `POST /api/v1/price/check`

Manueller Preis-Check fuer eine einzelne ASIN. Ruft Keepa API auf.

```bash
curl -X POST http://localhost:8000/api/v1/price/check \
  -H "Content-Type: application/json" \
  -d '{"asin": "B07W6JN8V8"}'
```

```json
{
  "asin": "B07W6JN8V8",
  "title": "Logitech K380 Multi-Device Bluetooth Tastatur",
  "current_price": 49.99,
  "list_price": 59.99,
  "rating": 4.5,
  "category": "340843031",
  "timestamp": "2026-02-20T12:00:00"
}
```

**Kosten:** ~15 Keepa-Tokens

#### `POST /api/v1/price/check-all`

Alle aktiven Watches pruefen. Wie ein manueller Scheduler-Durchlauf.

```bash
curl -X POST http://localhost:8000/api/v1/price/check-all
```

```json
{
  "status": "completed",
  "watches_checked": 12,
  "price_changes": 3,
  "alerts_triggered": 1,
  "timestamp": "2026-02-20T12:05:00"
}
```

**Achtung:** Kostet 15 Tokens PRO Watch. Bei 50 Watches = 750 Tokens.

---

### Deals

#### `POST /api/v1/deals/search`

Deal-Suche via Keepa API (oder Product-Fallback).

```bash
curl -X POST http://localhost:8000/api/v1/deals/search \
  -H "Content-Type: application/json" \
  -d '{
    "categories": ["340843031"],
    "min_discount": 20,
    "max_discount": 80,
    "min_price": 15,
    "max_price": 200,
    "min_rating": 4.0
  }'
```

```json
[
  {
    "asin": "B07W6JN8V8",
    "title": "Logitech K380 QWERTZ",
    "current_price": 39.99,
    "list_price": 59.99,
    "discount_percent": 33.4,
    "rating": 4.5,
    "reviews": 1847,
    "prime_eligible": true,
    "url": "https://amazon.de/dp/B07W6JN8V8",
    "deal_score": 8.7,
    "source": "product_api"
  }
]
```

**Fehler:**
- 429 — Token Limit erreicht
- 403 — Deal-API nicht verfuegbar (NoDealAccessError)

#### `POST /api/v1/deals/es-search`

Deal-Suche via Elasticsearch (Volltextsuche + Aggregationen).

```bash
curl -X POST http://localhost:8000/api/v1/deals/es-search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "logitech mechanisch",
    "min_discount": 15,
    "min_rating": 4.0,
    "page": 0,
    "size": 10
  }'
```

```json
{
  "total": 47,
  "deals": [
    {
      "asin": "B07W6JN8V8",
      "title": "Logitech MX Mechanical",
      "current_price": 119.99,
      "discount_percent": 25.0,
      "_score": 12.5
    }
  ],
  "aggregations": {
    "by_category": { ... },
    "by_domain": { ... },
    "price_stats": { "min": 15.99, "max": 299.99, "avg": 65.50 },
    "discount_stats": { "min": 15.0, "max": 70.0, "avg": 32.5 }
  }
}
```

**Features:**
- `multi_match` mit Fuzziness (Tippfehler-tolerant)
- `title` wird 3x hoeher gewichtet als description
- Aggregationen fuer Dashboard-Widgets
- Sortierung: deal_score desc, dann timestamp desc

#### `GET /api/v1/deals/aggregations?min_discount=20&min_rating=4`

Deal-Statistiken aus Elasticsearch.

#### `POST /api/v1/deals/index`

Manuell ein Deal-Dokument in Elasticsearch indexieren.

---

### Preis-Historie

#### `GET /api/v1/prices/{asin}/history?days=30`

Preishistorie aus PostgreSQL (price_history Tabelle).

```bash
curl "http://localhost:8000/api/v1/prices/B07W6JN8V8/history?days=30"
```

```json
{
  "asin": "B07W6JN8V8",
  "history": [
    {"price": 49.99, "recorded_at": "2026-02-01T12:00:00", "source": "kafka_deals"},
    {"price": 45.99, "recorded_at": "2026-02-05T12:00:00", "source": null},
    {"price": 39.99, "recorded_at": "2026-02-10T12:00:00", "source": "kafka_deals"}
  ],
  "count": 3,
  "period_days": 30
}
```

#### `GET /api/v1/prices/{asin}/stats`

Preis-Statistiken aus Elasticsearch (Aggregationen).

```bash
curl "http://localhost:8000/api/v1/prices/B07W6JN8V8/stats"
```

```json
{
  "asin": "B07W6JN8V8",
  "min": 29.99,
  "max": 59.99,
  "avg": 42.50,
  "current": 39.99,
  "data_points": 47,
  "price_over_time": [
    {"date": "2026-02-01", "avg_price": 49.99, "min_price": 45.99, "max_price": 52.99}
  ]
}
```

---

### Token & Rate Limit

#### `GET /api/v1/tokens`

Token Bucket Status (intern, nicht Keepa API).

#### `GET /api/v1/rate-limit`

Rate Limit Status direkt von der Keepa API.

---

## Pydantic Models

Alle Request/Response Models sind in `src/api/main.py` definiert:

| Model | Verwendung |
|-------|-----------|
| `WatchCreateRequest` | POST /watches Body |
| `WatchResponse` | GET/POST /watches Response |
| `WatchDeleteResponse` | DELETE /watches Response |
| `DealSearchRequest` | POST /deals/search Body |
| `DealResponse` | POST /deals/search Response Items |
| `ElasticsearchDealSearchRequest` | POST /deals/es-search Body |
| `ElasticsearchDealResponse` | POST /deals/es-search Response |
| `HealthResponse` | GET /health Response |
| `PriceCheckRequest` | POST /price/check Body |
| `PriceCheckResponse` | POST /price/check Response |
| `TriggerCheckResponse` | POST /price/check-all Response |

---

## Konfiguration

In `src/config.py`:
```python
app_name: str = "Keeper System"
debug: bool = False
log_level: str = "INFO"
```

In `docker-compose.yml`:
```yaml
app:
  build: .
  ports:
    - "8000:8000"
  command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

---

## Fehlerbehandlung

| HTTP Status | Bedeutung | Wann |
|-------------|-----------|------|
| 200 | OK | Erfolgreiche Abfrage |
| 201 | Created | Watch erstellt |
| 400 | Bad Request | Ungueltige ASIN, fehlende Parameter |
| 403 | Forbidden | Deal-API nicht im Plan |
| 404 | Not Found | ASIN nicht gefunden, Watch nicht gefunden |
| 429 | Too Many Requests | Keepa Token Limit |
| 500 | Internal Error | Unerwarteter Fehler |
| 503 | Service Unavailable | Elasticsearch nicht erreichbar |

---

## Testen

```bash
# Health Check
curl http://localhost:8000/health

# Watch erstellen
curl -X POST "http://localhost:8000/api/v1/watches?user_id=00000000-0000-0000-0000-000000000001" \
  -H "Content-Type: application/json" \
  -d '{"asin": "B07W6JN8V8", "target_price": 45.00}'

# Preis checken
curl -X POST http://localhost:8000/api/v1/price/check \
  -H "Content-Type: application/json" \
  -d '{"asin": "B07W6JN8V8"}'

# Elasticsearch Deal-Suche
curl -X POST http://localhost:8000/api/v1/deals/es-search \
  -H "Content-Type: application/json" \
  -d '{"query": "cherry tastatur", "min_discount": 10}'
```
