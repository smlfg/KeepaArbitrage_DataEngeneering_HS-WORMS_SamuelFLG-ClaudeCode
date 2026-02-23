# Docker — Referenz fuer das Keeper System

## Ueberblick

Das gesamte System laeuft in Docker Compose mit 8 Services. Du brauchst nur `docker-compose up -d` und alles startet in der richtigen Reihenfolge.

**Docker Compose Version:** 3.8
**Datei:** `docker-compose.yml`

---

## Services & Architektur

```
┌─────────────────────────────────────────────────────┐
│                    DOCKER NETWORK                     │
│                                                       │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐   │
│  │   app    │───→│  kafka   │───→│elasticsearch │   │
│  │ :8000    │    │ :29092   │    │   :9200      │   │
│  └────┬─────┘    └────┬─────┘    └──────┬───────┘   │
│       │               │                 │            │
│  ┌────┴─────┐    ┌────┴─────┐    ┌──────┴───────┐   │
│  │scheduler │    │zookeeper │    │   kibana     │   │
│  │ (no port)│    │  :2181   │    │    :5601     │   │
│  └────┬─────┘    └──────────┘    └──────────────┘   │
│       │                                              │
│  ┌────┴─────┐    ┌──────────────┐                   │
│  │    db    │    │kafka-connect │                   │
│  │  :5432   │    │    :8083     │                   │
│  └──────────┘    └──────────────┘                   │
└─────────────────────────────────────────────────────┘
```

---

## Service-Referenz

### 1. `app` — FastAPI Application

| Eigenschaft | Wert |
|-------------|------|
| **Build** | `./Dockerfile` (Python 3.11-slim) |
| **Port** | 8000 (extern) → 8000 (intern) |
| **Abhaengigkeiten** | db, kafka, elasticsearch |
| **Restart** | unless-stopped |

```yaml
app:
  build: .
  ports:
    - "8000:8000"
  environment:
    - KEEPA_API_KEY=${KEEPA_API_KEY}
    - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/keeper
    - KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    - ELASTICSEARCH_URL=http://elasticsearch:9200
  depends_on:
    - db
    - kafka
    - elasticsearch
  volumes:
    - ./src:/app/src          # Live-Reload fuer Entwicklung
    - ./prompts:/app/prompts
```

**Zugriff:** http://localhost:8000/docs (Swagger UI)

### 2. `scheduler` — Hintergrund-Scheduler

| Eigenschaft | Wert |
|-------------|------|
| **Build** | `./Dockerfile` (gleich wie app) |
| **Command** | `python -m src.scheduler` |
| **Port** | Keiner (Hintergrundprozess) |
| **Abhaengigkeiten** | db, kafka, elasticsearch |

```yaml
scheduler:
  build: .
  command: python -m src.scheduler
  environment:
    # Gleiche Env-Vars wie app
  depends_on:
    - db
    - kafka
    - elasticsearch
```

**Rolle:** Fuehrt alle 6 Stunden Preis-Checks durch, sammelt Deals, sendet Alerts.

### 3. `db` — PostgreSQL

| Eigenschaft | Wert |
|-------------|------|
| **Image** | `postgres:15-alpine` |
| **Port** | 5432 |
| **User/Pass** | postgres/postgres |
| **Datenbank** | keeper |
| **Volume** | `postgres_data` (persistent) |

```yaml
db:
  image: postgres:15-alpine
  environment:
    - POSTGRES_USER=postgres
    - POSTGRES_PASSWORD=postgres
    - POSTGRES_DB=keeper
  volumes:
    - postgres_data:/var/lib/postgresql/data
  ports:
    - "5432:5432"
```

**Verbinden:**
```bash
# Aus dem Host
psql -h localhost -U postgres -d keeper

# Aus einem anderen Container
psql -h db -U postgres -d keeper
```

### 4. `zookeeper` — Kafka Coordination

| Eigenschaft | Wert |
|-------------|------|
| **Image** | `confluentinc/cp-zookeeper:7.5.0` |
| **Port** | 2181 |
| **Volume** | `zookeeper_data` |

```yaml
zookeeper:
  image: confluentinc/cp-zookeeper:7.5.0
  environment:
    ZOOKEEPER_CLIENT_PORT: 2181
    ZOOKEEPER_TICK_TIME: 2000
```

**Warum?** Kafka braucht Zookeeper fuer Cluster-Koordination (Leader Election, Topic-Metadata). Neuere Kafka-Versionen (3.5+) haben KRaft als Alternative, aber wir nutzen die Confluent 7.5 Distribution die noch Zookeeper braucht.

### 5. `kafka` — Message Broker

| Eigenschaft | Wert |
|-------------|------|
| **Image** | `confluentinc/cp-kafka:7.5.0` |
| **Ports** | 9092 (extern), 29092 (intern) |
| **Abhaengigkeit** | zookeeper |
| **Volume** | `kafka_data` |
| **Retention** | 168 Stunden (7 Tage) |

```yaml
kafka:
  image: confluentinc/cp-kafka:7.5.0
  depends_on:
    - zookeeper
  ports:
    - "9092:9092"
  environment:
    KAFKA_BROKER_ID: 1
    KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
    KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
    KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
    KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
    KAFKA_LOG_RETENTION_HOURS: 168
```

**Zwei Listener:**
- `kafka:29092` (PLAINTEXT) — fuer Container-zu-Container Kommunikation
- `localhost:9092` (PLAINTEXT_HOST) — fuer Tools auf dem Host

**Auto-Topic-Creation:** Topics werden automatisch beim ersten Publish erstellt.

### 6. `elasticsearch` — Such-Engine

| Eigenschaft | Wert |
|-------------|------|
| **Image** | `docker.elastic.co/elasticsearch/elasticsearch:8.11.0` |
| **Ports** | 9200 (REST), 9300 (Transport) |
| **Heap** | 512 MB |
| **Security** | Deaktiviert |
| **Volume** | `elasticsearch_data` |

Siehe `docs/ELASTICSEARCH.md` fuer Details.

### 7. `kibana` — Dashboards

| Eigenschaft | Wert |
|-------------|------|
| **Image** | `docker.elastic.co/kibana/kibana:8.11.0` |
| **Port** | 5601 |
| **Abhaengigkeit** | elasticsearch |

**Zugriff:** http://localhost:5601

### 8. `kafka-connect` — Connector Bridge

| Eigenschaft | Wert |
|-------------|------|
| **Image** | `confluentinc/cp-kafka-connect:7.5.0` |
| **Port** | 8083 |
| **Abhaengigkeiten** | kafka, elasticsearch |

```yaml
kafka-connect:
  environment:
    CONNECT_BOOTSTRAP_SERVERS: kafka:29092
    CONNECT_GROUP_ID: kafka-connect
    CONNECT_CONFIG_STORAGE_TOPIC: connect-configs
    CONNECT_OFFSET_STORAGE_TOPIC: connect-offsets
    CONNECT_STATUS_STORAGE_TOPIC: connect-status
    CONNECT_KEY_CONVERTER: org.apache.kafka.connect.json.JsonConverter
    CONNECT_VALUE_CONVERTER: org.apache.kafka.connect.json.JsonConverter
```

**Rolle:** Optional. Kann Kafka-Topics automatisch nach Elasticsearch spiegeln. Aktuell machen wir das manuell im Code.

---

## Volumes (Persistenz)

```yaml
volumes:
  postgres_data:         # Datenbank-Dateien
  zookeeper_data:        # Zookeeper-State
  kafka_data:            # Kafka-Message-Logs
  elasticsearch_data:    # ES-Index-Dateien
```

Alle Volumes sind **named volumes** — Docker verwaltet den Speicherort. Daten bleiben erhalten wenn Container neu gestartet werden.

**Volumes loeschen:**
```bash
# VORSICHT: Loescht alle Daten!
docker-compose down -v
```

---

## Startup-Reihenfolge

Docker Compose startet abhaengige Services automatisch:

```
1. zookeeper (keine Abhaengigkeiten)
2. db (keine Abhaengigkeiten)
3. kafka (wartet auf zookeeper)
4. elasticsearch (keine Abhaengigkeiten)
5. kibana (wartet auf elasticsearch)
6. kafka-connect (wartet auf kafka + elasticsearch)
7. app (wartet auf db + kafka + elasticsearch)
8. scheduler (wartet auf db + kafka + elasticsearch)
```

**Wichtig:** `depends_on` wartet nur auf Container-Start, NICHT auf Service-Bereitschaft. Der Scheduler hat deshalb eigene Retry-Logik:

```python
# src/scheduler.py: Startup-Sequenz
# Phase 1: Kafka producers (muss vor consumers laufen)
# Phase 2: Elasticsearch (unabhaengig von Kafka)
# Phase 3: Kafka consumers (erst nach producers)
# Phase 4: Deal collector (erst wenn alles laeuft)
```

---

## Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Base Image:** `python:3.11-slim` (~150MB statt ~900MB fuer das volle Image)
**Default CMD:** Startet die FastAPI-App. Der Scheduler ueberschreibt das mit `command: python -m src.scheduler`.

---

## Environment Variables

Alle Env-Vars werden ueber `${VAR}` Syntax aus der `.env` Datei geladen:

```env
# .env
KEEPA_API_KEY=dein-api-key
SMTP_HOST=smtp.gmail.com
SMTP_USER=dein@email.com
SMTP_PASSWORD=app-passwort
TELEGRAM_BOT_TOKEN=dein-token
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
```

**Interne Verbindungen** (Container-zu-Container) sind fest konfiguriert:
- DB: `postgresql+asyncpg://postgres:postgres@db:5432/keeper`
- Kafka: `kafka:29092`
- ES: `http://elasticsearch:9200`

---

## Haeufige Befehle

```bash
# Alles starten
docker-compose up -d

# Logs eines Services folgen
docker-compose logs -f scheduler
docker-compose logs -f app

# Service neustarten (Code-Aenderung laden)
docker-compose restart scheduler

# Alles neu bauen (nach requirements.txt Aenderung)
docker-compose up -d --build

# Status aller Container
docker-compose ps

# In einen Container reinschauen
docker-compose exec app bash
docker-compose exec db psql -U postgres -d keeper

# Resource-Verbrauch
docker stats

# Alles stoppen
docker-compose down

# Alles stoppen + Daten loeschen
docker-compose down -v
```

---

## Netzwerk

Docker Compose erstellt automatisch ein Netzwerk (`keepa_default` o.ae.) in dem alle Container per Servicename erreichbar sind:

- `app` kann `db`, `kafka`, `elasticsearch` per Name ansprechen
- `scheduler` kann dasselbe
- Von ausserhalb (dein Host) nutzt du `localhost:PORT`

---

## Live-Reload (Development)

Die Volumes mounten Source-Code direkt in die Container:

```yaml
volumes:
  - ./src:/app/src
  - ./prompts:/app/prompts
```

Das heisst: Aenderungen an Python-Dateien sind sofort im Container sichtbar. **Aber:** Du musst den Container trotzdem neustarten damit Python die neuen Module laedt:

```bash
docker-compose restart scheduler
```

Fuer die FastAPI-App mit Auto-Reload:
```bash
docker-compose exec app uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Troubleshooting

### Container startet nicht
```bash
docker-compose logs SERVICE_NAME
```

### "Connection refused" zu Kafka
Kafka braucht ~30 Sekunden zum Starten. Der Scheduler hat Retry-Logik, aber wenn du manuell testest: warte.

### Elasticsearch "max virtual memory areas" Fehler
```bash
sudo sysctl -w vm.max_map_count=262144
# Permanent:
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```

### Zu viel Speicher
```bash
# Heap reduzieren
ES_JAVA_OPTS=-Xms256m -Xmx256m  # statt 512m

# Oder Services einzeln starten (ohne Kibana/Connect)
docker-compose up -d app db kafka zookeeper elasticsearch scheduler
```

### Datenbank zuruecksetzen
```bash
docker-compose down
docker volume rm keepaprojectsfordataengeneering3branchesmerge_postgres_data
docker-compose up -d
```
