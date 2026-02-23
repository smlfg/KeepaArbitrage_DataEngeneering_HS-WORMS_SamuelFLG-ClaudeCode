# Apache Kafka — Referenz fuer das Keeper System

## Was macht Kafka hier?

Kafka ist der **Event-Streaming-Layer** zwischen Daten-Erfassung und Verarbeitung. Statt Preis-Updates direkt in die Datenbank zu schreiben, publizieren wir Events auf Topics. Consumer lesen diese Events und verarbeiten sie unabhaengig.

**Warum?**
- **Entkopplung** — Producer (Scheduler) und Consumer (DB-Writer, ES-Indexer) kennen sich nicht
- **Pufferung** — Wenn die DB kurz down ist, gehen keine Events verloren
- **Replay** — Consumer koennen alte Events neu verarbeiten (7 Tage Retention)
- **Skalierung** — Mehrere Consumer koennen parallel lesen

---

## Architektur

```
                    KAFKA CLUSTER
                    ┌──────────────────────────────────┐
                    │                                    │
  ┌─────────┐      │  Topic: price-updates              │      ┌───────────────┐
  │Scheduler│─────→│  [msg1][msg2][msg3][msg4]...       │─────→│PriceConsumer  │
  │         │      │                                    │      │→ PostgreSQL   │
  └─────────┘      │  Topic: deal-updates               │      └───────────────┘
       │           │  [msg1][msg2][msg3]...              │
       └──────────→│                                    │──→┌───────────────┐
                    │                                    │   │DealConsumer   │
                    └──────────────────────────────────┘   │→ PostgreSQL   │
                                                            │→ price_history│
                                                            └───────────────┘
```

---

## Topics

### `price-updates`

**Producer:** `PriceUpdateProducer` (`src/services/kafka_producer.py`)
**Consumer:** `PriceUpdateConsumer` (`src/services/kafka_consumer.py`)
**Consumer Group:** `keeper-consumer-group`

**Message-Format:**
```json
{
  "asin": "B07W6JN8V8",
  "product_title": "Logitech K380 Multi-Device QWERTZ",
  "current_price": 49.99,
  "target_price": 45.00,
  "previous_price": 59.99,
  "price_change": 16.67,
  "domain": "de",
  "currency": "EUR",
  "timestamp": "2026-02-20T12:30:00",
  "event_type": "price_update"
}
```

**Key:** ASIN (fuer Partitionierung — gleiche ASIN immer gleiche Partition)

**Was der Consumer macht:**
1. Liest Message aus Topic
2. Prueft ob ASIN ein `WatchedProduct` in der DB ist
3. Speichert Preis in `price_history` Tabelle
4. Wenn `current_price <= target_price * 1.01` → erstellt `PriceAlert`

### `deal-updates`

**Producer:** `DealUpdateProducer` (`src/services/kafka_producer.py`)
**Consumer:** `DealUpdateConsumer` (`src/services/kafka_consumer.py`)
**Consumer Group:** `keeper-consumer-group-deals`

**Message-Format:**
```json
{
  "asin": "B07W6JN8V8",
  "product_title": "Logitech K380 Multi-Device QWERTZ",
  "current_price": 39.99,
  "original_price": 59.99,
  "discount_percent": 33.4,
  "rating": 4.5,
  "review_count": 1847,
  "sales_rank": 1200,
  "domain": "de",
  "timestamp": "2026-02-20T12:30:00",
  "event_type": "deal_update"
}
```

**Was der Consumer macht:**
1. Liest Message
2. Ruft `record_deal_price()` auf → speichert in `price_history` + erstellt/aktualisiert `WatchedProduct`
3. So entsteht eine komplette Preishistorie aus Deal-Snapshots

---

## Producer (Schreiben)

**Datei:** `src/services/kafka_producer.py`

### PriceUpdateProducer

```python
class PriceUpdateProducer:
    def __init__(self):
        self.producer: Optional[AIOKafkaProducer] = None
        self.topic = "price-updates"  # aus config

    async def start(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers="kafka:29092",
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self.producer.start()

    async def send_price_update(self, asin, product_title, current_price, ...):
        message = { ... }
        result = await self.producer.send_and_wait(self.topic, key=asin, value=message)
        # result.partition, result.offset → wo die Message gelandet ist
```

**Wichtig:**
- `send_and_wait()` — blockiert bis Kafka das ACK schickt (at-least-once delivery)
- `key=asin` — Messages mit gleicher ASIN landen IMMER in gleicher Partition
- `value_serializer` — JSON-Encoding mit `default=str` (damit datetime etc. nicht crashen)

### DealUpdateProducer

Gleiche Struktur, aber Topic ist `deal-updates` und kein Key (Round-Robin Partitionierung).

### Singletons

```python
# Am Modulende — einmal erstellen, ueberall importieren
price_producer = PriceUpdateProducer()
deal_producer = DealUpdateProducer()
```

---

## Consumer (Lesen)

**Datei:** `src/services/kafka_consumer.py`

### PriceUpdateConsumer

```python
class PriceUpdateConsumer:
    def __init__(self, db_session_factory):
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.topic = "price-updates"
        self.group_id = "keeper-consumer-group"

    async def start(self):
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers="kafka:29092",
            group_id=self.group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="earliest",   # Bei neuem Consumer: von Anfang lesen
            enable_auto_commit=True,         # Offsets automatisch speichern
        )

    async def consume(self):
        while self.running:
            async for message in self.consumer:
                await self.process_message(message.value)
```

### Consumer Groups

Jeder Consumer hat eine `group_id`. Kafka verteilt Partitionen auf Consumer in der gleichen Gruppe:

- `keeper-consumer-group` — fuer Preis-Updates
- `keeper-consumer-group-deals` — fuer Deal-Updates

**Warum separate Gruppen?** Weil Preis- und Deal-Consumer verschiedene Logik haben und unabhaengig skalieren sollen.

---

## Wann wird was gesendet?

### Scheduler → Kafka Producer

```
run_price_check()
  └── Fuer jede Watch mit current_price > 0:
      └── price_producer.send_price_update()     → Topic: price-updates

collect_deals_to_elasticsearch()
  └── Fuer jeden gesammelten Deal:
      └── deal_producer.send_deal_update()        → Topic: deal-updates

run_daily_deal_reports()
  └── Fuer jeden gefilterten Deal:
      └── deal_producer.send_deal_update()        → Topic: deal-updates
```

---

## Kafka Konfiguration (docker-compose.yml)

```yaml
kafka:
  environment:
    KAFKA_BROKER_ID: 1
    KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
    KAFKA_ADVERTISED_LISTENERS: >
      PLAINTEXT://kafka:29092,
      PLAINTEXT_HOST://localhost:9092
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: >
      PLAINTEXT:PLAINTEXT,
      PLAINTEXT_HOST:PLAINTEXT
    KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
    KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
    KAFKA_LOG_RETENTION_HOURS: 168
```

### Listener-Erklaerung

| Listener | Adresse | Wofuer |
|----------|---------|--------|
| `PLAINTEXT` | `kafka:29092` | Container-intern (app, scheduler, connect) |
| `PLAINTEXT_HOST` | `localhost:9092` | Zugriff vom Host (CLI-Tools, Debugging) |

### Retention

`KAFKA_LOG_RETENTION_HOURS: 168` = 7 Tage. Messages aelter als 7 Tage werden automatisch geloescht.

---

## Fehlerbehandlung

### Producer-Fehler

```python
try:
    result = await self.producer.send_and_wait(self.topic, key=asin, value=message)
except KafkaError as e:
    logger.error(f"Failed to send: {e}")
    return False
```

- `KafkaError` — Broker nicht erreichbar, Topic existiert nicht, etc.
- Producer gibt `False` zurueck, Scheduler loggt Fehler und macht weiter

### Consumer-Fehler

```python
async def consume(self):
    while self.running:
        try:
            async for message in self.consumer:
                await self.process_message(message.value)
        except Exception as e:
            logger.error(f"Consumer error: {e}")
            if self.running:
                await asyncio.sleep(5)  # 5s warten, dann retry
```

- Consumer reconnected automatisch nach 5 Sekunden
- Auto-Commit: Offset wird alle paar Sekunden gespeichert
- **At-least-once** Delivery: Bei Crash koennen Messages doppelt verarbeitet werden (ist OK fuer uns — Preise werden ueberschrieben)

---

## Kafka Connect (Optional)

```yaml
kafka-connect:
  image: confluentinc/cp-kafka-connect:7.5.0
  environment:
    CONNECT_BOOTSTRAP_SERVERS: kafka:29092
    CONNECT_GROUP_ID: kafka-connect
    CONNECT_CONFIG_STORAGE_TOPIC: connect-configs
    CONNECT_OFFSET_STORAGE_TOPIC: connect-offsets
    CONNECT_STATUS_STORAGE_TOPIC: connect-status
    CONNECT_KEY_CONVERTER: org.apache.kafka.connect.json.JsonConverter
    CONNECT_VALUE_CONVERTER: org.apache.kafka.connect.json.JsonConverter
    CONNECT_ELASTICSEARCH_URL: http://elasticsearch:9200
```

**Idee:** Kafka Connect kann Topics automatisch nach Elasticsearch spiegeln (Elasticsearch Sink Connector). Aktuell machen wir das manuell im Code (`es_service.index_deal_update()`), aber Connect waere die sauberere Loesung fuer Produktion.

---

## Nuetzliche CLI-Befehle

```bash
# Topics auflisten
docker-compose exec kafka kafka-topics --list --bootstrap-server localhost:29092

# Topic-Details (Partitionen, Replicas)
docker-compose exec kafka kafka-topics --describe --topic price-updates --bootstrap-server localhost:29092

# Messages lesen (von Anfang)
docker-compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:29092 \
  --topic price-updates \
  --from-beginning \
  --max-messages 5

# Messages lesen (live, ab jetzt)
docker-compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:29092 \
  --topic deal-updates

# Consumer Groups anzeigen
docker-compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:29092 \
  --list

# Consumer Group Details (Lag!)
docker-compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:29092 \
  --describe \
  --group keeper-consumer-group

# Topic manuell erstellen (normalerweise auto-create)
docker-compose exec kafka kafka-topics --create \
  --bootstrap-server localhost:29092 \
  --topic test-topic \
  --partitions 3 \
  --replication-factor 1

# Messages zaehlen
docker-compose exec kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:29092 \
  --topic price-updates
```

---

## Konfiguration in Python

In `src/config.py`:
```python
kafka_bootstrap_servers: str = "localhost:9092"
kafka_topic_prices: str = "price-updates"
kafka_topic_deals: str = "deal-updates"
kafka_consumer_group: str = "keeper-consumer-group"
```

**Achtung:** Im Docker-Compose ist `kafka:29092` die interne Adresse (fuer Container). `localhost:9092` ist fuer den Host. Die Config hat `localhost:9092` als Default fuer lokale Entwicklung.

---

## Wichtige Konzepte

### Partitionen

Topics bestehen aus Partitionen. Bei uns: 1 Partition pro Topic (Single-Broker Setup). Fuer Skalierung: mehr Partitionen = mehr parallele Consumer.

### Consumer Groups

Alle Consumer mit gleicher `group_id` teilen sich die Partitionen. Wenn wir 3 Partitionen und 3 Consumer haben, verarbeitet jeder Consumer genau 1 Partition.

### Offsets

Jeder Consumer merkt sich die letzte gelesene Position (Offset). Bei `auto_commit=True` wird der Offset regelmaessig gespeichert. Bei Neustart liest der Consumer ab dem letzten Offset weiter.

### At-Least-Once vs. Exactly-Once

Wir nutzen **at-least-once**: Eine Message kann im Fehlerfall doppelt verarbeitet werden. Fuer Preis-Updates ist das OK (idempotent: gleicher Preis wird nochmal geschrieben).

### Message Ordering

Messages mit gleichem Key (ASIN) landen in gleicher Partition → **Reihenfolge garantiert** innerhalb einer ASIN. Preis-Updates fuer B07W6JN8V8 kommen immer in der richtigen Reihenfolge an.
