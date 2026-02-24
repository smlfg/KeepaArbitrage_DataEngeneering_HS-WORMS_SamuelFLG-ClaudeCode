# Elasticsearch — Referenz fuer das Keeper System

## Was macht Elasticsearch hier?

Elasticsearch ist unser **Such- und Analyse-Layer**. Waehrend PostgreSQL die "Source of Truth" ist (ACID-konform, relational), ist Elasticsearch optimiert fuer:

- **Volltextsuche** — "Finde alle Logitech-Tastaturen unter 50 EUR"
- **Aggregationen** — "Durchschnittspreis pro Marke", "Top-10 Rabatte"
- **Zeitreihen** — Preisverlauf ueber Tage/Wochen als Histogramm
- **Kibana-Dashboards** — Visuelle Auswertung ohne SQL

**Version:** 8.11.0 (Single-Node, Security deaktiviert)
**Port:** 9200 (HTTP API), 9300 (Cluster-Kommunikation)

---

## Unsere Indices

### 1. `keeper-prices` — Preis-Updates

Speichert jeden Preis-Snapshot den der Scheduler erfasst.

**Datei:** `src/services/elasticsearch_service.py:19-45`

```json
{
  "mappings": {
    "properties": {
      "asin":                 { "type": "keyword" },
      "product_title":        { "type": "text", "analyzer": "standard",
                                "fields": { "keyword": { "type": "keyword" } } },
      "current_price":        { "type": "float" },
      "target_price":         { "type": "float" },
      "previous_price":       { "type": "float" },
      "price_change_percent": { "type": "float" },
      "domain":               { "type": "keyword" },
      "currency":             { "type": "keyword" },
      "timestamp":            { "type": "date" },
      "event_type":           { "type": "keyword" }
    }
  },
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "index.max_result_window": 50000
  }
}
```

**Wichtige Felder:**
- `asin` als `keyword` — exakte Suche, keine Tokenisierung
- `product_title` als `text` + `keyword` — Volltextsuche UND exakte Filterung
- `max_result_window: 50000` — erlaubt tiefes Paging (Standard ist 10000)

### 2. `keeper-deals` — Deal-Snapshots

Speichert gesammelte Deals mit Scoring.

**Datei:** `src/services/elasticsearch_service.py:47-89`

```json
{
  "settings": {
    "analysis": {
      "analyzer": {
        "deal_analyzer": {
          "type": "custom",
          "tokenizer": "standard",
          "filter": ["lowercase", "german_stemmer", "asciifolding"]
        }
      },
      "filter": {
        "german_stemmer": { "type": "stemmer", "language": "german" }
      }
    }
  },
  "mappings": {
    "properties": {
      "asin":             { "type": "keyword" },
      "title":            { "type": "text", "analyzer": "deal_analyzer",
                            "fields": {
                              "keyword": { "type": "keyword" },
                              "suggest": { "type": "completion" }
                            } },
      "description":      { "type": "text", "analyzer": "deal_analyzer" },
      "current_price":    { "type": "float" },
      "original_price":   { "type": "float" },
      "discount_percent": { "type": "float" },
      "rating":           { "type": "float" },
      "review_count":     { "type": "integer" },
      "sales_rank":       { "type": "integer" },
      "domain":           { "type": "keyword" },
      "category":         { "type": "keyword" },
      "prime_eligible":   { "type": "boolean" },
      "url":              { "type": "keyword" },
      "deal_score":       { "type": "float" },
      "timestamp":        { "type": "date" },
      "event_type":       { "type": "keyword" }
    }
  }
}
```

**Besonderheiten:**
- **`deal_analyzer`** — Custom Analyzer mit deutschem Stemmer + ASCII-Folding
  - "Tastatur" und "Tastaturen" matchen
  - "Mechanische" und "mechanisch" matchen
  - Umlaute: "Geraet" matcht "Gerät" (via asciifolding)
- **`suggest` (completion)** — fuer Autocomplete/Type-Ahead in Kibana

---

## Service-Klasse

**Datei:** `src/services/elasticsearch_service.py`

```python
class ElasticsearchService:
    def __init__(self):
        self.client: Optional[AsyncElasticsearch] = None
        self.prices_index = "keeper-prices"      # aus config
        self.deals_index = "keeper-deals"         # aus config

    async def connect(self):
        """Verbindung + automatische Index-Erstellung"""
        self.client = AsyncElasticsearch([settings.elasticsearch_url])
        await self._create_indices()

    async def close(self):
        """Sauberes Trennen"""
```

**Singleton:** `es_service = ElasticsearchService()` am Modulende.

---

## Methoden-Referenz

### Schreiben (Indexierung)

| Methode | Was | Wann aufgerufen |
|---------|-----|-----------------|
| `index_price_update(data)` | Einzelnes Preis-Dokument indexieren | Scheduler: nach jedem Preis-Check |
| `index_deal_update(data)` | Einzelnes Deal-Dokument indexieren | Deal-Collector: pro Deal |

Beide nutzen `await self.client.index(index=..., document=...)` — kein Bulk!
Fuer hoehere Durchsaetze: Bulk API waere besser, aber bei 10-50 Docs/Zyklus reicht Einzelindexierung.

### Lesen (Suche)

#### `search_prices(asin, min_price, max_price, domain, from_date, to_date, page, size)`

```python
# Beispiel: Alle Preise fuer eine ASIN im Januar
results = await es_service.search_prices(
    asin="B07W6JN8V8",
    from_date=datetime(2026, 1, 1),
    to_date=datetime(2026, 1, 31),
    size=100
)
```

Baut eine `bool/must` Query mit optionalen Filtern. Sortiert nach `timestamp desc`.

#### `get_price_statistics(asin)`

Aggregation: min/max/avg Preis + Histogramm (10 EUR Buckets).

```python
stats = await es_service.get_price_statistics("B07W6JN8V8")
# {"price_stats": {"min": 29.99, "max": 59.99, "avg": 42.50, ...}}
```

#### `get_deal_aggregations(min_discount, min_rating, domain)`

Aggregation ueber alle Deals:

```python
aggs = await es_service.get_deal_aggregations(min_discount=20, min_rating=4.0)
# {"by_discount": [...], "by_domain": [...], "avg_price": 45.0, "avg_discount": 32.5}
```

#### `get_deal_price_stats(asin)`

Preisstatistiken fuer ein Produkt aus Deal-Snapshots:

```python
stats = await es_service.get_deal_price_stats("B07W6JN8V8")
# {
#   "min": 29.99, "max": 59.99, "avg": 42.50,
#   "current": 35.99,
#   "data_points": 47,
#   "price_over_time": [
#     {"date": "2026-01-15", "avg_price": 39.99, "min_price": 35.99, ...},
#     ...
#   ]
# }
```

Nutzt `date_histogram` mit `calendar_interval: "day"` fuer Tages-Aggregation.

### Loeschen

#### `delete_old_data(days=90)`

Loescht Dokumente aelter als N Tage aus BEIDEN Indices:

```python
deleted = await es_service.delete_old_data(days=90)
# "Deleted 1523 old documents"
```

---

## Datenfluss

```
Scheduler (Preis-Check)
    ↓
es_service.index_price_update({asin, price, ...})
    ↓
keeper-prices Index
    ↓
Kibana Dashboard / API search_prices()

Deal Collector (Hintergrund-Task)
    ↓
es_service.index_deal_update({asin, title, discount, score, ...})
    ↓
keeper-deals Index
    ↓
Kibana Dashboard / API get_deal_aggregations()
```

---

## Docker-Konfiguration

```yaml
elasticsearch:
  image: docker.elastic.co/elasticsearch/elasticsearch:8.11.0
  environment:
    - discovery.type=single-node       # Kein Cluster, nur 1 Node
    - xpack.security.enabled=false     # Kein Auth (Dev-Setup)
    - "ES_JAVA_OPTS=-Xms512m -Xmx512m"  # 512MB Heap
  ports:
    - "9200:9200"    # REST API
    - "9300:9300"    # Node-to-Node (nicht genutzt bei single-node)
  volumes:
    - elasticsearch_data:/usr/share/elasticsearch/data  # Persistenz
```

**Wichtig:**
- `xpack.security.enabled=false` — Kein Passwort noetig. Nur fuer lokale Entwicklung!
- `512m Heap` — reicht fuer ~100k Dokumente. Fuer Produktion: mindestens 2GB

---

## Kibana

```yaml
kibana:
  image: docker.elastic.co/kibana/kibana:8.11.0
  ports:
    - "5601:5601"
  environment:
    - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
```

**URL:** http://localhost:5601

### Kibana einrichten

Data Views und Dashboards werden automatisch per `kibana/setup-kibana.sh` importiert.
Alles Weitere: **[KIBANA.md](KIBANA.md)** — Discover, KQL-Queries, Dashboard-Guide, Dev Tools.

---

## Nuetzliche curl-Befehle

```bash
# Cluster-Health pruefen
curl -s http://localhost:9200/_cluster/health | python3 -m json.tool

# Indices auflisten
curl -s http://localhost:9200/_cat/indices?v

# Dokumente zaehlen
curl -s http://localhost:9200/keeper-deals/_count

# Letzte 5 Deals anzeigen
curl -s 'http://localhost:9200/keeper-deals/_search?size=5&sort=timestamp:desc' | python3 -m json.tool

# Suche nach ASIN
curl -s 'http://localhost:9200/keeper-deals/_search' -H 'Content-Type: application/json' -d '{
  "query": {"term": {"asin": "B07W6JN8V8"}},
  "sort": [{"timestamp": "desc"}],
  "size": 10
}' | python3 -m json.tool

# Durchschnittspreis pro Domain
curl -s 'http://localhost:9200/keeper-deals/_search' -H 'Content-Type: application/json' -d '{
  "size": 0,
  "aggs": {
    "by_domain": {
      "terms": {"field": "domain"},
      "aggs": {
        "avg_price": {"avg": {"field": "current_price"}}
      }
    }
  }
}' | python3 -m json.tool

# Index loeschen (Vorsicht!)
curl -X DELETE http://localhost:9200/keeper-deals

# Alte Daten loeschen (aelter als 90 Tage)
curl -X POST 'http://localhost:9200/keeper-deals/_delete_by_query' -H 'Content-Type: application/json' -d '{
  "query": {"range": {"timestamp": {"lt": "now-90d"}}}
}'
```

---

## Konfiguration

In `.env`:
```
ELASTICSEARCH_URL=http://localhost:9200
```

In `src/config.py`:
```python
elasticsearch_url: str = "http://localhost:9200"
elasticsearch_index_prices: str = "keeper-prices"
elasticsearch_index_deals: str = "keeper-deals"
```

---

## Wichtige Konzepte

### Keyword vs. Text

- **`keyword`** = Exakte Werte. "B07W6JN8V8" wird NICHT tokenisiert. Perfekt fuer IDs, Domains, ASINs.
- **`text`** = Wird in Tokens zerlegt. "Logitech K380 Keyboard" → ["logitech", "k380", "keyboard"]. Perfekt fuer Suche.
- **Multi-Field** = Beides: `title` als text fuer Suche + `title.keyword` fuer exakte Aggregation.

### Eventual Consistency

ES ist **nicht ACID**. Nach einem `index()` Call ist das Dokument erst nach einem Refresh sichtbar (Standard: 1 Sekunde). Fuer uns kein Problem — wir lesen nie sofort nach dem Schreiben.

### Single-Node Limitierung

- Kein Failover (Node stirbt = Daten weg bis Neustart)
- 1 Shard, 0 Replicas = minimaler Overhead
- Fuer Produktion: mindestens 3 Nodes mit 1 Replica
