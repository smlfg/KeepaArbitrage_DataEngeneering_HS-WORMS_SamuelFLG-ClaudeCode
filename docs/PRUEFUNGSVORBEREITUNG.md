# Keeper System -- Dein Prüfungs-Cheatsheet

> Dieses Dokument ist dein Rettungsring. Nicht zum Abschreiben, sondern zum Verstehen.
> Lies es laut. Erklaer es deinem Spiegel. Wenn du es in eigenen Worten sagen kannst, hast du's verstanden.

---

## Teil 1: Die 5 Kernfunktionen -- Was sie tun und WARUM

### 1. `asyncio.gather()` mit Semaphore -- `scheduler.py:195-204`

```python
semaphore = asyncio.Semaphore(5)

async def _check_with_semaphore(watch):
    async with semaphore:
        return await self.check_single_price(watch)

check_results = await asyncio.gather(
    *[_check_with_semaphore(w) for w in watches],
    return_exceptions=True,
)
```

**Was es tut:** `asyncio.gather()` startet mehrere asynchrone Aufgaben gleichzeitig -- in unserem Fall Preisabfragen fuer verschiedene Produkte. Statt nacheinander 50 Produkte abzufragen (50 x 2 Sekunden = 100 Sekunden), laufen sie parallel. Der `Semaphore(5)` begrenzt dabei die gleichzeitigen Verbindungen auf 5, damit wir die Keepa API nicht ueberlasten.

**Analogie:** Stell dir eine Supermarkt-Kasse vor. Ohne `gather` stehst du in EINER Schlange und wartest nacheinander. Mit `gather` oeffnest du 50 Kassen gleichzeitig. Der Semaphore sagt: "Aber nur 5 Kassen duerfen gleichzeitig offen sein" -- weil du nicht unendlich Kassierer hast (= API-Limits).

**Pruefer-Frage:** "Was passiert wenn `return_exceptions=True` fehlt?"
**Antwort:** Dann wuerde ein einziger fehlgeschlagener API-Call ALLE laufenden Calls abbrechen. Mit `return_exceptions=True` kriegen wir stattdessen das Exception-Objekt zurueck und koennen es graceful behandeln -- die anderen Calls laufen weiter.

---

### 2. `asyncpg` statt `psycopg2` -- `config.py:36`

```python
database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/keeper"
```

**Was es tut:** `asyncpg` ist ein asynchroner PostgreSQL-Treiber. Im Gegensatz zu `psycopg2` (synchron) blockiert `asyncpg` nicht den Event Loop waehrend einer Datenbankabfrage. Das heisst: Waehrend eine DB-Query auf Antwort wartet, kann unser Server andere HTTP-Requests bearbeiten.

**Analogie:** `psycopg2` ist wie ein Kellner, der in der Kueche steht und wartet bis das Essen fertig ist -- in der Zeit bedient er niemanden. `asyncpg` ist wie ein Kellner, der die Bestellung abgibt und sofort zum naechsten Tisch geht. Wenn das Essen fertig ist, wird er benachrichtigt und bringt es zum Tisch.

**Warum das fuer unser Projekt wichtig ist:** Unser Scheduler macht gleichzeitig DB-Writes (PriceHistory speichern), Kafka-Sends (Events publishen) und Keepa API-Calls. Wenn der DB-Write synchron waere, muessten alle anderen Operationen warten. Mit async laufen sie ueberlappend -- darum auch `AsyncElasticsearch` und `AIOKafkaProducer`.

---

### 3. Kafka Consumer Groups & Offsets -- `kafka_consumer.py:26-33`

```python
self.consumer = AIOKafkaConsumer(
    self.topic,
    bootstrap_servers=settings.kafka_bootstrap_servers,
    group_id=self.group_id,
    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    auto_offset_reset="earliest",
    enable_auto_commit=True,
)
```

**Was ein Offset ist:** Kafka speichert Nachrichten in einem Log (wie ein Tagebuch mit nummerierten Seiten). Der Offset ist die Seitennummer, die ein Consumer zuletzt gelesen hat. Wenn der Consumer abstuerzt und neustartet, fragt er Kafka: "Wo war ich?" und Kafka antwortet: "Seite 4.327" -- und der Consumer liest ab dort weiter.

**Was eine Consumer Group ist:** Mehrere Consumer koennen in einer Gruppe zusammenarbeiten. Wenn das Topic 4 Partitions hat und die Group 2 Consumer, bekommt jeder Consumer 2 Partitions. Faellt ein Consumer aus, uebernimmt der andere seine Partitions automatisch (Rebalancing). Das ist wie ein Team, das sich die Arbeit aufteilt -- faellt einer aus, springen die anderen ein.

**Warum wir zwei Consumer Groups haben:** `keeper-consumer-group` fuer Preise schreibt in PostgreSQL (PriceHistory). `keeper-consumer-group-deals` fuer Deals loggt nur. Zwei getrennte Gruppen, weil sie unterschiedliche Verarbeitungslogik und -geschwindigkeit haben. Wenn die Deal-Verarbeitung langsam ist, bremst sie nicht die Preis-Verarbeitung.

**`auto_offset_reset="earliest"`:** Wenn ein neuer Consumer das erste Mal startet (kein gespeicherter Offset), liest er ALLE Nachrichten von Anfang an. `latest` wuerde nur neue Nachrichten lesen und die alten ignorieren.

---

### 4. Elasticsearch `single-node` -- `docker-compose.yml:74`

```yaml
elasticsearch:
  environment:
    - discovery.type=single-node
    - xpack.security.enabled=false
    - "ES_JAVA_OPTS=-Xms512m -Xmx512m"
```

**Was `discovery.type=single-node` bedeutet:** Elasticsearch ist als Cluster-System designed -- normalerweise suchen sich mehrere Nodes gegenseitig und bilden einen Cluster. `single-node` schaltet diese Cluster-Erkennung ab und sagt: "Ich bin allein, kein Cluster noetig." Das ist der Development/Demo-Modus.

**Warum das fuer uns reicht:** Unser Datenvolumen ist klein (~100 Deals pro Zyklus). Ein einzelner Node mit 512MB RAM kann hunderttausende Dokumente speichern und durchsuchen. Erst ab Millionen von Dokumenten oder wenn Hochverfuegbarkeit noetig ist, braucht man einen Cluster.

**Was in Production anders waere:** 3+ Nodes mit `number_of_replicas: 1` statt 0. Jedes Dokument wird auf mindestens 2 Nodes gespeichert. Faellt ein Node aus, sind die Daten noch auf dem anderen. Unsere Index-Settings (`number_of_shards: 1, number_of_replicas: 0`) sind bewusst auf Single-Node optimiert -- in Production waere das ein SPOF (Single Point of Failure).

---

### 5. `_normalize_deal()` -- `deal_finder.py:295-338`

```python
def _normalize_deal(self, deal: Dict[str, Any]) -> Dict[str, Any]:
    current_price = float(deal.get("current_price", deal.get("currentPrice", 0)) or 0)
    list_price = float(
        deal.get("list_price", deal.get("listPrice", deal.get("original_price", current_price)))
        or current_price
    )
    discount_percent = deal.get("discount_percent", deal.get("discountPercent"))
    if discount_percent is None:
        if list_price > current_price > 0:
            discount_percent = round((1 - current_price / list_price) * 100, 1)
```

**Was es tut:** Diese Funktion ist der "Uebersetzer" zwischen verschiedenen Datenformaten. Die Keepa API liefert manchmal `currentPrice` (camelCase), manchmal `current_price` (snake_case), manchmal `original_price` statt `list_price`. `_normalize_deal()` akzeptiert ALLE Varianten und produziert immer das gleiche Output-Format.

**Warum das noetig ist:** Wenn du Daten aus verschiedenen API-Endpunkten oder Quellen zusammenfuehrst (hier: Product API vs. Deal API), haben sie oft unterschiedliche Feldnamen fuer die gleiche Information. Ohne Normalisierung muesste jede nachfolgende Funktion (Scoring, Filtering, ES-Indexing) alle Varianten kennen -- ein Wartungs-Albtraum.

**Die Discount-Berechnung:** Wenn kein Discount-Wert mitgeliefert wird, berechnet die Funktion ihn selbst: `(1 - aktueller_preis / listen_preis) * 100`. Beispiel: Listenpreis 100EUR, aktueller Preis 70EUR -> `(1 - 70/100) * 100 = 30%` Rabatt. Die Bedingung `list_price > current_price > 0` verhindert Division durch Null und negative Rabatte.

---

## Teil 2: Die 5 Pruefer-Killfragen -- mit Antworten zum Auswendiglernen

### F1: "Was macht `asyncio.gather()` in eurem Scheduler?"

> Es startet parallele Preisabfragen fuer alle aktiven Watches gleichzeitig. Statt 50 Produkte nacheinander abzufragen, laufen sie parallel mit einem Semaphore, der auf 5 gleichzeitige Connections begrenzt. `return_exceptions=True` sorgt dafuer, dass ein fehlgeschlagener Call nicht alle anderen abbricht -- wir bekommen die Exception als Rueckgabewert und koennen sie einzeln behandeln.

### F2: "Warum `asyncpg` statt `psycopg2`?"

> Weil unser gesamtes System asynchron ist. FastAPI, Keepa-Client, Kafka, Elasticsearch -- alles nutzt `async/await`. Ein synchroner DB-Treiber wuerde den Event Loop blockieren: Waehrend einer 100ms DB-Query koennten wir keine HTTP-Requests bearbeiten. `asyncpg` gibt die Kontrolle an den Event Loop zurueck waehrend die DB arbeitet, sodass andere Tasks weiterlaufen koennen.

### F3: "Was ist ein Kafka Offset und was passiert bei Consumer-Crash?"

> Ein Offset ist die Position eines Consumers im Kafka Log -- wie eine Seitennummer in einem Buch. Jede Nachricht hat eine eindeutige Offset-Nummer pro Partition. Bei einem Crash hat Kafka den letzten Committed Offset gespeichert. Beim Neustart liest der Consumer ab diesem Offset weiter -- keine Nachricht geht verloren. Das ist der fundamentale Unterschied zu klassischen Message Queues, wo eine Nachricht nach dem Lesen weg ist.

### F4: "Warum `single-node` bei Elasticsearch?"

> Das ist eine bewusste Development-Entscheidung. `discovery.type=single-node` schaltet die Cluster-Erkennung ab, weil wir nur einen Node betreiben. Unser Datenvolumen (~100-500 Dokumente pro Zyklus) braucht keinen Cluster. In Production wuerden wir auf 3 Nodes gehen, `number_of_replicas` auf 1 setzen, und den Discovery-Type entfernen, damit die Nodes sich automatisch finden und einen Cluster bilden.

### F5: "Was macht `_normalize_deal` und warum braucht ihr das?"

> Die Keepa API liefert Daten in unterschiedlichen Formaten je nach Endpunkt -- mal `currentPrice` (camelCase), mal `current_price` (snake_case). `_normalize_deal` ist der Uebersetzer: Es akzeptiert alle Varianten und produziert ein einheitliches Format mit berechneten Feldern wie `discount_percent`. Ohne Normalisierung muessten Scoring, Filtering und Elasticsearch-Indexing alle moeglichen Feldnamen kennen -- das waere unwartbar.

### F6: "Eure project-deep-dive.md meldet 3 kritische Bugs. Sind die noch aktuell?"

> Nein. Wir haben alle drei als Teil eines manuellen Code-Reviews untersucht und widerlegt:
>
> 1. **"Dual DB schemas"** — Das war ein Artefakt aus dem Git-Merge von 3 Branches. Der Sync-Stack existiert nur im `Input/`-Referenzordner und wird nirgendwo importiert. Die aktive Codebase nutzt ein einziges async Schema.
>
> 2. **"Missing await in deals endpoint"** — Alle `await`s sind korrekt. Der Deals-Endpoint nutzt den Product-API-Fallback (weil `/deals` auf unserem Keepa-Plan nicht verfuegbar ist), und der ist korrekt implementiert.
>
> 3. **"DELETE watch endpoint returns success without DB mutation"** — Der Endpoint macht ein echtes Soft-Delete: Er setzt `status = 'inactive'` und committed die Aenderung in die Datenbank.
>
> **Das zeigt eine wichtige Lektion:** Automatisierte Code-Reviews (auch von LLMs) muessen manuell verifiziert werden. Die Deep-Dive-Analyse wurde VOR der Branch-Konsolidierung erstellt und bezog sich teilweise auf Code der im finalen Merge nicht mehr aktiv war.

---

## Teil 3: Die "Warum"-Fragen zu jeder Technologie

### PostgreSQL -- 3 "Warum"-Fragen

**1. Warum brauchen wir ACID-Transaktionen?**

Stell dir vor, der Scheduler schreibt gerade eine PriceHistory und einen PriceAlert gleichzeitig. Ohne Transaktion koennte der Alert gespeichert werden, aber die PriceHistory nicht (z.B. bei Verbindungsabbruch). Dann haetten wir einen Alert ohne zugehoerigen Preis-Eintrag -- inkonsistente Daten. ACID garantiert: Entweder wird beides gespeichert, oder nichts.

**2. Warum `asyncpg` statt direktem SQL?**

SQLAlchemy mit `asyncpg` gibt uns ein ORM (Object-Relational Mapping) -- wir arbeiten mit Python-Objekten statt mit Raw SQL. Das bedeutet: Typ-Sicherheit (ein `float` bleibt ein `float`), automatische SQL-Injection-Praevention, und einfachere Migrationen. Der `async`-Teil sorgt dafuer, dass DB-Queries den Event Loop nicht blockieren.

**3. Warum nicht einfach alles in Elasticsearch speichern?**

Elasticsearch hat keine echten Transaktionen. Wenn wir einen Deal in ES indexieren und der Prozess mittendrin abstuerzt, kann das Dokument teilweise geschrieben sein. PostgreSQL ist unsere "Source of Truth" -- die einzige Stelle, der wir zu 100% vertrauen. ES ist der "schnelle Suchindex", der im schlimmsten Fall aus PG neu aufgebaut werden kann.

### Kafka -- 3 "Warum"-Fragen

**1. Warum nicht direkt in die DB schreiben?**

Entkopplung. Der Scheduler (Producer) muss nicht wissen, wer die Daten verarbeitet. Heute schreiben wir in PG und ES -- morgen koennten wir einen dritten Consumer hinzufuegen (z.B. ML-Pipeline) ohne den Scheduler zu aendern. Ausserdem: Wenn die DB kurz nicht erreichbar ist, puffert Kafka die Nachrichten statt sie zu verlieren.

**2. Warum tracken Consumer Groups Offsets?**

Damit bei einem Neustart keine Nachrichten verloren gehen oder doppelt verarbeitet werden. Jeder Consumer committed seinen Offset regelmaessig. Bei Crash: Lese ab letztem Offset weiter. Bei Consumer Groups wird das noch wichtiger: Wenn Consumer A ausfaellt, uebernimmt Consumer B seine Partitions und liest ab A's letztem Offset weiter.

**3. Warum `KAFKA_LOG_RETENTION_HOURS: 168` (7 Tage)?**

Das ist unser Safety-Net. Wenn die Consumer ein Wochenende lang down sind, gehen keine Nachrichten verloren. Am Montag holt der Consumer 7 Tage Nachrichten nach. Laenger als 7 Tage aufzuheben waere Platzverschwendung -- unsere Preis-Daten aelter als eine Woche sind in PostgreSQL archiviert.

### Elasticsearch -- 3 "Warum"-Fragen

**1. Warum ein eigener `deal_analyzer` mit German Stemmer?**

```yaml
"deal_analyzer": {
    "type": "custom",
    "tokenizer": "standard",
    "filter": ["lowercase", "german_stemmer", "asciifolding"]
}
```

Weil unsere Deals deutsche Produkttitel haben. Der German Stemmer reduziert "Kopfhoerer" und "Kopfhoerern" auf den gleichen Stamm -- so findet eine Suche nach "Kopfhoerer" auch Produkte die "Kopfhoerern" im Titel haben. `asciifolding` wandelt Umlaute um, damit "Ueberraschung" auch bei einer Suche nach "Uberraschung" gefunden wird.

**2. Warum `number_of_shards: 1`?**

Shards sind die Einheit der Parallelitaet in ES. Mehr Shards = mehr parallele Suchen moeglich. Aber: Jeder Shard hat Overhead (Speicher, File Handles). Bei unserem Datenvolumen waere mehr als 1 Shard Over-Engineering. Die Faustregel ist: ~50GB pro Shard. Unsere Daten sind im MB-Bereich.

**3. Warum Dual-Write (PG + ES) statt nur ES?**

ES ist "eventually consistent" -- nach einem Write kann es Millisekunden dauern bis das Dokument suchbar ist. Fuer einen Preis-Alert, der sofort reagieren muss, ist das zu langsam. PG ist sofort konsistent nach Commit. Ausserdem: ES hat kein ACID, kein Rollback, keine Foreign Keys. Es ist ein Suchindex, keine Datenbank.

---

## Teil 4: Der Datenfluss -- Von API-Call bis Dashboard

So fliesst ein einzelner Preis-Check durch das System:

```
1. Scheduler-Loop triggert alle 6h
   |
2. get_active_watches() -- holt alle aktiven Watches aus PostgreSQL
   |
3. asyncio.gather() -- fragt fuer JEDES Watch die Keepa API parallel ab
   |              (begrenzt durch Semaphore auf 5 gleichzeitige Calls)
   |
4. Fuer JEDES Ergebnis passiert:
   |
   |-- a) update_watch_price() --> PostgreSQL (aktueller Preis update)
   |
   |-- b) price_producer.send_price_update() --> Kafka Topic "price-updates"
   |         |
   |         +--> PriceUpdateConsumer liest Topic
   |                |
   |                +--> _save_price_history() --> PostgreSQL (History-Eintrag)
   |                +--> _create_alert() --> PostgreSQL (wenn Preis < Zielpreis)
   |
   |-- c) es_service.index_price_update() --> Elasticsearch "keeper-prices"
   |         |
   |         +--> Kibana Dashboard zeigt Preis-Trend
   |
   |-- d) Wenn alert_triggered:
           create_price_alert() --> PostgreSQL
           alert_dispatcher.dispatch_alert() --> Email/Telegram/Discord
```

---

## Teil 5: Vokabelliste -- Begriffe die du kennen MUSST

| Begriff | Erklaerung | Wo im Code |
|---------|-----------|------------|
| **ASIN** | Amazon Standard Identification Number -- 10-stellige Produkt-ID | `deal_finder.py:94` |
| **Event Loop** | Pythons "Dirigent" der async Tasks -- entscheidet wer dran ist | `scheduler.py:656` |
| **Semaphore** | Zaehler der begrenzt, wie viele Tasks gleichzeitig laufen duerfen | `scheduler.py:195` |
| **Token Bucket** | Rate-Limiting-Algorithmus: Tokens werden ueber Zeit aufgefuellt, jeder API-Call "verbraucht" Tokens | `keepa_api.py` |
| **Offset** | Position eines Kafka-Consumers im Log -- wie ein Lesezeichen | `kafka_consumer.py:31` |
| **Consumer Group** | Gruppe von Consumern die sich Partitions aufteilen | `kafka_consumer.py:29` |
| **Shard** | Grundeinheit der Datenverteilung in Elasticsearch | `elasticsearch_service.py:34` |
| **Replica** | Kopie eines Shards auf einem anderen Node fuer Ausfallsicherheit | `elasticsearch_service.py:35` |
| **ACID** | Atomicity, Consistency, Isolation, Durability -- Garantien einer DB-Transaktion | PG-Konzept |
| **Dual-Write** | Gleichzeitiges Schreiben in zwei Systeme (PG + ES) | `scheduler.py:243-258` |
| **camelCase** | `currentPrice` -- JavaScript/API-Stil | Keepa API Response |
| **snake_case** | `current_price` -- Python-Stil | Unser Code |
| **Normalisierung** | Verschiedene Formate in ein einheitliches Format umwandeln | `deal_finder.py:295` |
| **Index (ES)** | Wie eine Datenbank-Tabelle in Elasticsearch | `elasticsearch_service.py:89-90` |
| **ORM** | Object-Relational Mapping -- Python-Objekte statt SQL | SQLAlchemy |
| **ASGI** | Async Server Gateway Interface -- der Standard fuer async Python-Webserver | Uvicorn/FastAPI |

---

## Teil 6: Dein 5-Minuten Warm-Up vor der Pruefung

Lies das hier **laut** 10 Minuten vor der Pruefung:

1. **Unser System holt alle 6 Stunden Amazon-Preise ueber die Keepa API.**
2. **Der Scheduler startet parallele API-Calls mit `asyncio.gather()`, begrenzt auf 5 gleichzeitige Connections.**
3. **Jeder Preis geht in drei Systeme: PostgreSQL (Persistenz), Kafka (Event-Streaming), Elasticsearch (Suche).**
4. **Kafka entkoppelt Producer und Consumer -- wenn die DB kurz down ist, puffert Kafka die Nachrichten.**
5. **Elasticsearch mit Kibana visualisiert Preis-Trends und Deal-Statistiken in Echtzeit-Dashboards.**
6. **`_normalize_deal()` uebersetzt verschiedene API-Formate in ein einheitliches Schema -- camelCase wird zu snake_case.**
7. **Wir verwenden `asyncpg` statt `psycopg2`, weil alles async ist -- ein synchroner DB-Treiber wuerde den Event Loop blockieren.**
8. **Kafka Offsets sind wie Lesezeichen -- bei Consumer-Crash wird ab dem letzten Offset weitergelesen.**
9. **Elasticsearch laeuft als Single-Node weil unser Datenvolumen klein ist -- in Production waeren es 3+ Nodes.**
10. **PostgreSQL ist Source of Truth, Elasticsearch ist der schnelle Suchindex -- bei ES-Ausfall verlieren wir keine Daten.**
11. **Die project-deep-dive.md meldete 3 "HIGH"-Bugs — alle drei waren False Positives nach manueller Verifikation. Automatisierte Reviews muessen IMMER manuell geprueft werden.**
12. **Performance-Fix: `price_monitor.py` und `scheduler.py` (_collect_seed_asin_deals) wurden von sequentiellen Loops auf `asyncio.gather()` + `Semaphore(5)` umgestellt — gleiche Logik, aber 5x schneller.**

---

*Erstellt am 2026-02-20 -- Keeper System Pruefungsvorbereitung*
*Basierend auf echtem Code, nicht auf Vermutungen.*
