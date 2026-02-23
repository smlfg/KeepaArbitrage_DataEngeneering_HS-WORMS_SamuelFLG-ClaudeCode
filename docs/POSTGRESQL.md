# PostgreSQL — Referenz fuer das Keeper System

## Rolle im System

PostgreSQL ist die **Source of Truth** — die einzige Stelle wo Daten garantiert korrekt und dauerhaft gespeichert sind. Elasticsearch kann man jederzeit loeschen und aus PostgreSQL neu aufbauen. Umgekehrt geht das nicht.

**Version:** 15 (Alpine)
**ORM:** SQLAlchemy 2.0+ mit async Support (asyncpg Driver)
**Datei:** `src/services/database.py`

---

## Schema-Ueberblick

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│    users     │────→│ watched_products  │────→│ price_history│
│              │     │                   │     │              │
│ id (UUID)    │     │ id (UUID)         │     │ id (UUID)    │
│ email        │     │ user_id (FK)      │     │ watch_id (FK)│
│ telegram_id  │     │ asin              │     │ price        │
│ discord_hook │     │ target_price      │     │ recorded_at  │
│ created_at   │     │ current_price     │     │ buy_box_seller│
│ is_active    │     │ status            │     └──────────────┘
└──────┬───────┘     │ last_checked_at   │
       │             └────────┬──────────┘     ┌──────────────┐
       │                      │                │ price_alerts │
       │                      └───────────────→│              │
       │                                       │ id (UUID)    │
       │                                       │ watch_id (FK)│
       │                                       │ trigger_price│
       │                                       │ target_price │
       │                                       │ status       │
       │                                       │ sent_at      │
       │                                       └──────────────┘
       │
       │             ┌──────────────────┐     ┌──────────────┐
       └────────────→│  deal_filters    │────→│ deal_reports │
                     │                   │     │              │
                     │ id (UUID)         │     │ id (UUID)    │
                     │ user_id (FK)      │     │ filter_id(FK)│
                     │ name              │     │ deals_data   │
                     │ min_discount      │     │ generated_at │
                     │ max_price         │     └──────────────┘
                     │ min_rating        │
                     └──────────────────┘

┌──────────────────┐
│ collected_deals  │  (Standalone — kein FK zu users)
│                   │
│ id (UUID)         │
│ asin              │
│ title             │
│ current_price     │
│ original_price    │
│ discount_percent  │
│ rating            │
│ sales_rank        │
│ domain            │
│ deal_score        │
│ collected_at      │
└──────────────────┘
```

---

## Tabellen im Detail

### `users`

Multi-Tenant Support. Jeder User hat eigene Watches und Filter.

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | Automatisch generiert |
| `email` | VARCHAR(255), UNIQUE | Login-Identifier |
| `telegram_chat_id` | VARCHAR(50) | Fuer Telegram-Alerts |
| `discord_webhook` | VARCHAR(500) | Fuer Discord-Alerts |
| `created_at` | TIMESTAMP | Erstellungszeitpunkt |
| `is_active` | BOOLEAN | Soft-Delete Flag |

**System-User:** `00000000-0000-0000-0000-000000000001` wird automatisch erstellt fuer auto-getrackede Produkte (via `get_or_create_system_user()`).

### `watched_products`

Produkte die ein User ueberwacht. Jede ASIN-User-Kombination ist unique.

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK → users) | Wer ueberwacht |
| `asin` | VARCHAR(10), INDEX | Amazon ASIN |
| `product_name` | VARCHAR(500) | Produkttitel |
| `target_price` | FLOAT | Alert wenn Preis darunter |
| `current_price` | FLOAT | Letzter bekannter Preis |
| `volatility_score` | FLOAT | Preis-Schwankung (0-1) |
| `status` | ENUM | ACTIVE / PAUSED / INACTIVE |
| `last_checked_at` | TIMESTAMP | Letzter Preis-Check |
| `last_price_change` | TIMESTAMP | Letzte Preisaenderung |

**Indices:**
- `idx_watched_products_user_asin` (user_id, asin) — UNIQUE

**Status-Enum:**
- `ACTIVE` — wird regelmaessig gecheckt
- `PAUSED` — temporaer pausiert
- `INACTIVE` — soft-deleted

### `price_history`

Zeitreihe aller erfassten Preise. Die "Goldmine" fuer Analyse.

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | |
| `watch_id` | UUID (FK → watched_products) | |
| `price` | FLOAT | Preis in EUR |
| `buy_box_seller` | VARCHAR(100) | Wer die Buy Box hat (oder "backfill", "kafka_deals") |
| `recorded_at` | TIMESTAMP, INDEX | Wann erfasst |

**Indices:**
- `idx_price_history_watch_time` (watch_id, recorded_at) — fuer schnelle Zeitreihen-Abfragen

**Datenquellen:**
- Scheduler Preis-Check → `buy_box_seller` = Seller-Name oder NULL
- Kafka Deal Consumer → `buy_box_seller` = "kafka_deals"
- Backfill → `buy_box_seller` = "backfill"

### `price_alerts`

Ausgeloeste Alarme wenn Preise unter Target fallen.

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | |
| `watch_id` | UUID (FK → watched_products) | |
| `triggered_price` | FLOAT | Preis der den Alert ausgeloest hat |
| `target_price` | FLOAT | Zielpreis des Users |
| `old_price` | FLOAT | Vorheriger Preis |
| `new_price` | FLOAT | Neuer Preis |
| `discount_percent` | FLOAT | Berechneter Rabatt |
| `status` | ENUM | PENDING / SENT / FAILED |
| `triggered_at` | TIMESTAMP | Wann ausgeloest |
| `sent_at` | TIMESTAMP | Wann versendet |
| `notification_channel` | VARCHAR(20) | email / telegram / discord |

**Alert-Trigger-Logik:** `current_price <= target_price * 1.01` (1% Toleranz)

### `deal_filters`

User-definierte Suchfilter fuer Deal-Reports.

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK → users) | |
| `name` | VARCHAR(100) | "Gaming Keyboards unter 80 EUR" |
| `categories` | JSON | Amazon-Kategorie-IDs |
| `min_price` / `max_price` | FLOAT | Preisbereich |
| `min_discount` / `max_discount` | INT | Rabattbereich (%) |
| `min_rating` | FLOAT | Mindestbewertung |
| `is_active` | BOOLEAN | Filter aktiv? |

### `deal_reports`

Generierte Deal-Reports (HTML-Emails).

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | |
| `filter_id` | UUID (FK → deal_filters) | Welcher Filter |
| `deals_data` | JSON | Deal-Daten als JSON |
| `generated_at` | TIMESTAMP | Wann generiert |
| `sent_at` | TIMESTAMP | Wann versendet |

### `collected_deals`

Rohdaten aus der Keepa API. Kein FK zu users — systemweit gesammelt.

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | UUID (PK) | |
| `asin` | VARCHAR(10), INDEX | |
| `title` | VARCHAR(500) | Produkttitel |
| `current_price` | FLOAT | Aktueller Preis |
| `original_price` | FLOAT | Listenpreis / UVP |
| `discount_percent` | FLOAT | Berechneter Rabatt |
| `rating` | FLOAT | 1.0 - 5.0 |
| `review_count` | INT | Anzahl Reviews |
| `sales_rank` | INT | Amazon-Verkaufsrang |
| `domain` | VARCHAR(10) | "de", "uk", "fr" etc. |
| `category` | VARCHAR(100) | Kategorie-Name |
| `url` | VARCHAR(200) | Amazon-URL |
| `prime_eligible` | BOOLEAN | Prime-berechtigt? |
| `deal_score` | FLOAT | Berechneter Deal-Score |
| `collected_at` | TIMESTAMP, INDEX | Erfassungszeitpunkt |

**Indices:**
- `idx_collected_deals_asin_collected` (asin, collected_at)
- `idx_collected_deals_discount` (discount_percent)
- `idx_collected_deals_price` (current_price)

---

## Async Engine Setup

```python
# src/services/database.py

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@db:5432/keeper"

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

**Wichtige Optionen:**
- `pool_pre_ping=True` — prueft ob Verbindung noch lebt bevor sie genutzt wird
- `expire_on_commit=False` — Objekte bleiben nach Commit nutzbar (kein Lazy-Load noetig)
- `echo=False` — kein SQL-Logging (fuer Debug: `echo=True`)

---

## Wichtige Datenbankoperationen

### Tabellen erstellen
```python
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```
Wird beim Scheduler-Start aufgerufen. Erstellt alle Tabellen falls sie nicht existieren.

### Watch erstellen
```python
watch = await create_watch(user_id="uuid...", asin="B07W6JN8V8", target_price=45.00)
```

### Preis aktualisieren
```python
watch = await update_watch_price(watch_id="uuid...", current_price=39.99)
# → Erstellt PriceHistory Record + aktualisiert WatchedProduct
```

### Auto-Tracking (System-User)
```python
watch_id = await ensure_tracked_product(asin="B07W6JN8V8", title="Logitech K380", current_price=39.99)
# → Erstellt System-User falls noetig
# → Erstellt WatchedProduct falls noetig
# → Gibt watch_id zurueck
```

### Deal-Preis speichern (aus Kafka)
```python
success = await record_deal_price(asin="B07W6JN8V8", price=39.99, title="Logitech K380", source="kafka_deals")
# → ensure_tracked_product() + PriceHistory Insert + Watch Update
```

### Batch-Deals speichern
```python
saved = await save_collected_deals_batch([
    {"asin": "B07W6JN8V8", "title": "...", "current_price": 39.99, ...},
    {"asin": "B09XXXX", "title": "...", "current_price": 79.99, ...},
])
# → Speichert alle in collected_deals Tabelle
```

### Backfill Price History
```python
total = await backfill_price_history_from_deals()
# → Liest alle collected_deals
# → Erstellt price_history Records daraus
# → Idempotent: ueberspringt wenn schon Daten da
```

### Beste Deals abfragen
```python
deals = await get_best_deals(min_discount=30, min_rating=4.0, max_price=100, limit=50)
# → CollectedDeal Objekte, sortiert nach discount_percent DESC
```

---

## Verbindung

### Docker-intern
```
postgresql+asyncpg://postgres:postgres@db:5432/keeper
```

### Vom Host
```
postgresql+asyncpg://postgres:postgres@localhost:5432/keeper
```

### psql
```bash
# Direkt
psql -h localhost -U postgres -d keeper

# Via Docker
docker-compose exec db psql -U postgres -d keeper
```

---

## Nuetzliche SQL-Queries

```sql
-- Alle aktiven Watches
SELECT asin, product_name, target_price, current_price, last_checked_at
FROM watched_products
WHERE status = 'ACTIVE'
ORDER BY last_checked_at DESC;

-- Preishistorie fuer eine ASIN
SELECT ph.price, ph.recorded_at, ph.buy_box_seller
FROM price_history ph
JOIN watched_products wp ON ph.watch_id = wp.id
WHERE wp.asin = 'B07W6JN8V8'
ORDER BY ph.recorded_at DESC
LIMIT 50;

-- Letzte 20 gesammelte Deals
SELECT asin, title, current_price, discount_percent, rating, domain, collected_at
FROM collected_deals
ORDER BY collected_at DESC
LIMIT 20;

-- Deals mit mehr als 30% Rabatt
SELECT asin, title, current_price, original_price, discount_percent
FROM collected_deals
WHERE discount_percent >= 30
  AND rating >= 4.0
ORDER BY discount_percent DESC;

-- Durchschnittspreis pro Domain
SELECT domain, COUNT(*), ROUND(AVG(current_price)::numeric, 2) as avg_price
FROM collected_deals
GROUP BY domain
ORDER BY avg_price;

-- Pending Alerts
SELECT pa.triggered_price, pa.target_price, pa.status,
       wp.asin, wp.product_name, u.email
FROM price_alerts pa
JOIN watched_products wp ON pa.watch_id = wp.id
JOIN users u ON wp.user_id = u.id
WHERE pa.status = 'PENDING';

-- Preis-Trend (Tagesdurchschnitte)
SELECT DATE(ph.recorded_at) as day,
       ROUND(AVG(ph.price)::numeric, 2) as avg_price,
       MIN(ph.price) as min_price,
       MAX(ph.price) as max_price
FROM price_history ph
JOIN watched_products wp ON ph.watch_id = wp.id
WHERE wp.asin = 'B07W6JN8V8'
GROUP BY DATE(ph.recorded_at)
ORDER BY day DESC;
```

---

## Konfiguration

In `.env`:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/keeper
```

In `src/config.py`:
```python
database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/keeper"
```

---

## Wichtige Konzepte

### UUID Primary Keys

Alle Tabellen nutzen UUIDs statt Auto-Increment Integer. Vorteile:
- Keine Kollisionen bei verteilten Systemen
- IDs koennen client-seitig generiert werden
- Nicht erratbar (Sicherheit)

### Soft Deletes

Watches werden nicht geloescht, sondern auf `INACTIVE` gesetzt:
```python
async def soft_delete_watch(watch_id, user_id):
    watch.status = WatchStatus.INACTIVE
```

### Session-Pattern

Jede Operation oeffnet eine eigene Session:
```python
async with async_session_maker() as session:
    # Query, Insert, Update
    await session.commit()
```

Das ist sicher fuer Concurrency — keine shared Transactions.

### Idempotenz

- `ensure_tracked_product()` — erstellt nur wenn nicht vorhanden
- `backfill_price_history_from_deals()` — ueberspringt wenn System-User schon History hat
- Doppelte Deals in `collected_deals` sind OK (verschiedene `collected_at`)
