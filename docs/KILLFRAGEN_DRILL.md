# Killfragen-Drill — 10 Pruefungsfragen mit Musterantworten

> Lies jede Antwort einmal laut, dann versuch sie in eigenen Worten wiederzugeben.
> Wenn du haengst → Zusammenfassungstabelle am Ende.

---

## Killfrage 1: "Erklaeren Sie den Datenfluss von der API bis zum Dashboard"

**Musterantwort (4 Saetze):**

> Der APScheduler triggert alle 6 Stunden einen Zyklus. Der Scheduler holt mit `asyncio.gather()` parallel Preisdaten von der Keepa REST API, begrenzt durch einen Semaphore auf 5 gleichzeitige Connections. Jeder Preis wird in drei Systeme geschrieben: PostgreSQL fuer persistente Speicherung (PriceHistory, Watches), Kafka als Event-Stream (Topic `price-updates` und `deal-updates`), und Elasticsearch fuer Volltextsuche und Aggregationen. Kibana liest aus Elasticsearch und visualisiert Preis-Trends und Deal-Statistiken als Dashboard.

**Merksatz:** `Keepa → Scheduler → Kafka → PG + ES → Kibana`

**Schwachstelle:** Koennte verwechseln, dass Kafka ZWISCHEN Scheduler und DB sitzt (als Puffer), nicht danach. Die Kafka Consumer schreiben in PG, der Scheduler schreibt direkt in ES.

---

## Killfrage 2: "Warum Kafka und nicht direkt in die DB schreiben?"

**Musterantwort (3 Saetze):**

> Entkopplung. Der Scheduler als Producer muss nicht wissen, wer die Daten verarbeitet — heute schreiben wir in PostgreSQL und Elasticsearch, morgen koennten wir einen dritten Consumer hinzufuegen (z.B. ML-Pipeline) ohne den Scheduler zu aendern. Wenn die DB kurz nicht erreichbar ist, puffert Kafka die Nachrichten mit 7 Tagen Retention (`KAFKA_LOG_RETENTION_HOURS=168`) statt sie zu verlieren. Ausserdem koennen die Consumer Groups `prices` und `deals` unabhaengig skaliert werden.

**Nachfrage-Killer:** "Warum nicht RabbitMQ?" → Kafka passt besser zu log-basierten Pipelines (Offset-basiert, replaybar). RabbitMQ loescht Messages nach dem Lesen — kein Replay moeglich.

**Schwachstelle:** Sicherstellen, dass du den Unterschied zwischen Kafka (Log-basiert, Replay) und klassischen Message Queues (Nachricht nach Lesen weg) erklaeren kannst.

---

## Killfrage 3: "Was passiert wenn ein Container ausfaellt?"

**Musterantwort (4 Saetze):**

> Alle Container haben `restart: unless-stopped` — Docker startet sie automatisch neu. Bei Kafka-Ausfall puffert das Log die Nachrichten; der Consumer liest beim Neustart ab dem letzten Committed Offset weiter — keine Nachricht geht verloren. Bei Elasticsearch-Ausfall verlieren wir keine Daten, weil PostgreSQL die Source of Truth ist — die Suche ist nur temporaer eingeschraenkt, ES kann aus PG neu aufgebaut werden. Bei DB-Ausfall cached Kafka die Events bis PostgreSQL wieder da ist.

**Nachfrage-Killer:** "Und wenn Kafka UND DB gleichzeitig ausfallen?" → Der Scheduler loggt Fehler, Daten gehen verloren. Fuer Production wuerde man Kafka Replication Factor > 1 setzen und PG Read Replicas nutzen.

**Schwachstelle:** Nicht vergessen: ES Single-Node ist ein SPOF — in Production waeren 3+ Nodes mit Replicas noetig.

---

## Killfrage 4: "Wie skaliert das System?"

**Musterantwort (4 Saetze):**

> Drei Hebel: Erstens Kafka — mehr Partitions pro Topic plus mehr Consumer in der Consumer Group ergibt horizontale Skalierung. Zweitens Elasticsearch — von Single-Node auf 3-Node-Cluster mit Sharding, Suchperformance skaliert linear. Drittens der Scheduler — Batch-Groesse erhoehen, kuerzere Intervalle, mehrere Scheduler-Instanzen mit Offset-Koordination. Der Bottleneck waere PostgreSQL — hier wuerden wir Read Replicas oder Partitionierung der PriceHistory-Tabelle nach Datum einsetzen.

**Nachfrage-Killer:** "Warum nicht einfach einen groesseren Server?" → Vertikale Skalierung hat Grenzen. Kafka und ES sind fuer horizontale Skalierung designed — mehr Nodes statt groessere Nodes.

**Schwachstelle:** Die konkrete Zahl kennen: Aktuell ~100 Nachrichten pro Zyklus, 1 Shard reicht. Faustregel: ~50GB pro Shard.

---

## Killfrage 5: "Was ist der Unterschied zwischen PostgreSQL und Elasticsearch hier?"

**Musterantwort (3 Saetze):**

> PostgreSQL ist unsere Source of Truth — ACID-Transaktionen garantieren Konsistenz bei Preis-History und Watches. Wenn ein Preis gespeichert wird, ist er da, mit Rollback bei Fehlern. Elasticsearch ist der schnelle Suchindex — "Zeige alle Deals unter 50 Euro mit Rating ueber 4.0 sortiert nach Discount" ist eine Zeile in ES, aber ein komplexer Join in SQL. Der Trade-off ist Dual-Write-Komplexitaet: Wir schreiben in beide Systeme, aber bei ES-Ausfall verlieren wir keine Daten.

**Nachfrage-Killer:** "Warum nicht alles in ES?" → ES hat keine echten Transaktionen, kein Rollback, keine Foreign Keys. Es ist ein Suchindex, keine Datenbank. Ein teilweise geschriebenes Dokument bei Crash ist moeglich.

**Schwachstelle:** Den Satz "PostgreSQL = Konsistenz, Elasticsearch = Performance" verinnerlichen. Nicht verwechseln wer Source of Truth ist.

---

## Killfrage 6: "Wie funktioniert die Preisueberwachung?"

**Musterantwort (4 Saetze):**

> Der Scheduler holt alle aktiven Watches aus PostgreSQL und fragt fuer jedes Watch die Keepa API ab. Die Keepa-Antwort liefert Preise als CSV-Arrays in Cent — wir extrahieren vier Preis-Typen: Amazon, New, Used und Warehouse Deal. Ein Token-Bucket-Algorithmus schuetzt vor API-Ueberlastung: Keepa gibt mit jeder Response die verbleibenden Tokens zurueck, unser Client wartet automatisch wenn Tokens unter den Schwellwert fallen. Wenn der aktuelle Preis unter den Zielpreis faellt, wird ein Alert generiert und ueber Email, Telegram oder Discord versendet.

**Nachfrage-Killer:** "Was ist ein Token Bucket?" → Rate-Limiting-Algorithmus: Tokens werden ueber Zeit aufgefuellt, jeder API-Call verbraucht Tokens. Wenn leer → warten bis neue Tokens da sind. Elegant weil adaptiv — passt sich automatisch an die API-Antwort an.

**Schwachstelle:** Die Preis-Hierarchie kennen: Deal-Preis = WHD > Used > New. Referenzpreis = Amazon > New > Used > WHD. Deals erst ab 10% Discount.

---

## Killfrage 7: "Was sind die Docker-Container und warum brauchen Sie jeden?"

**Musterantwort (4 Saetze):**

> Wir haben 7 Container fuer 6 Technologien. **app** (FastAPI auf Port 8000) — die REST API mit Swagger-Dokumentation. **db** (PostgreSQL 15) — relationale Datenhaltung mit ACID. **kafka** + **zookeeper** — Event Streaming fuer entkoppelte Datenverarbeitung. **elasticsearch** + **kibana** — Volltextsuche und Dashboard-Visualisierung. Dazu **scheduler** als separater Container der die periodischen Keepa-Abfragen ausfuehrt — getrennt von der API, damit ein Scheduler-Crash die API nicht runterzieht.

| Container | Image | Port | Zweck |
|-----------|-------|------|-------|
| app | Custom (Dockerfile) | 8000 | FastAPI REST API |
| db | postgres:15-alpine | 5432 | Relationale Datenhaltung |
| kafka | confluentinc/cp-kafka:7.5.0 | 9092 | Event Streaming |
| zookeeper | confluentinc/cp-zookeeper:7.5.0 | 2181 | Kafka Koordination |
| elasticsearch | elasticsearch:8.11.0 | 9200 | Suche + Aggregationen |
| kibana | kibana:8.11.0 | 5601 | Dashboards |
| scheduler | Custom (gleicher Build) | - | Periodische API-Abfragen |

**Schwachstelle:** Nicht vergessen: Es gibt auch **kibana-setup** (curl-Container fuer einmalige Kibana-Konfiguration, `restart: "no"`). Das sind also technisch 8 Services.

---

## Killfrage 8: "Wie testen Sie das System?"

**Musterantwort (3 Saetze):**

> Wir haben 262 Tests mit 0 Failures — das umfasst Unit Tests fuer einzelne Funktionen (z.B. `_normalize_deal`, Discount-Berechnung), Integrationstests fuer die Kafka Consumer/Producer Kommunikation und API-Endpoint-Tests mit FastAPI TestClient. Die Tests nutzen pytest mit async Support (`pytest-asyncio`) fuer die asynchronen Komponenten. Mocking wird fuer externe Abhaengigkeiten eingesetzt — Keepa API, Elasticsearch, Kafka — damit Tests ohne laufende Container ausfuehrbar sind.

**Nachfrage-Killer:** "Wie testen Sie die Kafka-Integration?" → Consumer/Producer Tests mit Mocks. Der Consumer bekommt vorbereitete Messages, wir pruefen ob der richtige DB-Write passiert. Kein echter Kafka-Broker noetig fuer Unit Tests.

**Schwachstelle:** Konkreten Test-Befehl kennen: `pytest` (konfiguriert in `pytest.ini`). Die hohe Testanzahl (262) ist ein starkes Argument — die meisten Studenten haben <20.

---

## Killfrage 9: "Was wuerden Sie anders machen?"

**Musterantwort (4 Saetze):**

> Erstens: Nur EINEN Keepa-Client statt zwei. Wir haben `keepa_api.py` (942 LOC, async) und `keepa_client.py` (726 LOC, sync) — Ergebnis von iterativem Vibe-Coding. Ein einziger Client mit ~400 LOC haette gereicht. Zweitens: Die drei "Agents" (DealFinder, PriceMonitor, AlertDispatcher) sind eigentlich einfache Funktionen ohne State — eine `transforms.py` mit 5 Funktionen waere sauberer gewesen. Drittens: Die 2.500 LOC Einmal-Scripts fuer Seed-Daten haetten nie ins Repo gehoert — die Daten liegen laengst in `data/`.

**Profi-Framing (auswendig lernen):**

> "Ja, der Code ist bewusst umfangreicher als noetig. Wir wollten die Technologien nicht nur benutzen, sondern verstehen. Deshalb haben wir z.B. einen Token-Bucket Rate Limiter selbst implementiert statt eine Library zu nehmen. In Production wuerde man das schlanker machen — aber fuer den Lerneffekt war die Tiefe wertvoll."

**Schwachstelle:** Das ist die gefaehrlichste Frage. NICHT defensiv werden. Ehrlich sein + mit Lerneffekt framen. Die Zahlen aus dem Audit kennen: 16.679 LOC statt ~6.000 minimal = 2.8x.

---

## Killfrage 10: "Erklaeren Sie Ihre Git-Strategie"

**Musterantwort (3 Saetze):**

> Wir hatten drei Branches die jeweils verschiedene Aspekte des Systems entwickelt haben — diese wurden in einem finalen Merge zusammengefuehrt. Die Strategie war Feature-basiert: Jeder Branch hatte eine eigene Verantwortlichkeit (z.B. API-Layer, Scheduler/Pipeline, Elasticsearch-Integration). Das erklaert auch warum manche Dateien doppelt existieren (z.B. zwei Keepa-Clients) — Artefakte aus dem Branch-Merge, die wir bewusst beibehalten haben statt riskante Refactorings kurz vor Abgabe zu machen.

**Nachfrage-Killer:** "Warum nicht aufgeraeumt?" → Code Freeze vor Abgabe. Funktionierende 262 Tests sind wichtiger als saubere Git-History. Aufraeum-Refactoring haette Tests brechen koennen.

**Schwachstelle:** Die `project-deep-dive.md` meldete 3 "HIGH"-Bugs die alle False Positives waren (nach Branch-Merge). Falls der Prof das sieht: "Automatisierte Code-Reviews muessen immer manuell verifiziert werden — das war eine wichtige Lektion."

---

## Quick-Reference: Zusammenfassungstabelle

| # | Frage (Stichwort) | Kern der Antwort |
|---|-------------------|------------------|
| 1 | Datenfluss | Keepa → Scheduler → Kafka → PG + ES → Kibana |
| 2 | Warum Kafka | Entkopplung + Puffer + Replay + unabhaengige Skalierung |
| 3 | Container faellt aus | `restart: unless-stopped` + Kafka-Offsets + PG ist Source of Truth |
| 4 | Skalierung | Kafka Partitions + ES Cluster + Scheduler Instances. Bottleneck = PG |
| 5 | PG vs ES | PG = ACID/Konsistenz. ES = Suche/Speed. Dual-Write, PG ist Source of Truth |
| 6 | Preisueberwachung | Token Bucket + 4 Preistypen + Zielpreis-Alert + Notifications |
| 7 | Container | 7+1 Services: app, db, kafka, zookeeper, ES, kibana, scheduler (+setup) |
| 8 | Testen | 262 Tests, pytest-asyncio, Mocks fuer externe Services |
| 9 | Anders machen | 1 Keepa-Client, Funktionen statt "Agents", weniger Scripts. Frame: Lerneffekt |
| 10 | Git-Strategie | 3 Feature-Branches → finaler Merge. Code Freeze vor Abgabe |

---

*Erstellt am 2026-02-24 — Keeper System Killfragen-Drill*
*Basierend auf ARCHITECTURE.md, PRUEFUNGSVORBEREITUNG.md, PIPELINE_FLOW.md, OVER_ENGINEERING_AUDIT.md, docker-compose.yml und config.py.*
