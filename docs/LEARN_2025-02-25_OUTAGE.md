# Was ich heute gelernt habe — PriceCheck-Ausfall, 25. Feb 2026

Aufbereitung der Session fuer spaeteres Ich. Jedes Konzept einzeln, mit Analogien.

---

## 1. Race Condition beim Container-Startup

**Was passiert ist:** Docker startet alle Container quasi gleichzeitig. Der Scheduler war sofort bereit, aber Kafka und Elasticsearch brauchen 10-30 Sekunden zum Hochfahren. Der Scheduler hat versucht zu connecten, bekam `Connection refused`, und hat das als "geht halt nicht" akzeptiert.

**Analogie:** Du kommst um 7:55 in die Uni, die Tuer ist noch zu. Du drehst dich um und gehst nach Hause. Nie wieder. Obwohl die Tuer um 8:00 aufgeht.

**Das Pattern heisst:** Race Condition — zwei Prozesse konkurrieren um Timing, und wer zuerst kommt verliert (weil der andere noch nicht bereit ist).

**Die Loesung:** `_ensure_connections()` — vor jedem 6h-Zyklus pruefen: "Ist Kafka da? Ist ES da? Nein? Nochmal probieren."

```python
# scheduler.py Zeile 283-285
while self.running:
    await self._ensure_connections()   # <-- DAS hat gefehlt
    await self.run_price_check()
```

**Warum der Deal-Collector das ueberlebt hat:** Der laeuft als eigener `asyncio.Task` mit `try/except` um jede einzelne Operation. Wenn eine Iteration fehlschlaegt, wartet er und probiert's nochmal. Das ist **per-Operation Error Handling** vs. **Main-Loop Error Handling**.

```
Deal-Collector:                    Price-Checker:
while True:                        while True:
    try:                               try:
        do_one_deal()                      check_ALL_prices()  # <-- alles oder nichts
    except:                            except:
        log_error()                        log_error()
        continue     # weiter!            # weiter, aber Kafka kaputt = alles still False
    sleep(interval)                    sleep(interval)
```

**Merksatz:** Self-Healing braucht Fehlerbehandlung auf der richtigen Granularitaet. Pro Task, nicht pro Loop.

---

## 2. Dual-Scheduler: Warum zwei Systeme laufen und keiner es merkt

**Was passiert ist:** Es gab ZWEI Scheduler parallel:

| System | Wo | ES-Index | Status |
|--------|----|----------|--------|
| systemd `keepa-scheduler.service` | `Input/` Verzeichnis | `keepa-deals` (1758 Docs) | Lief seit Monaten |
| Docker `scheduler` Container | Hauptprojekt | `keeper-deals` (927 Docs) | Unser Hauptsystem |

**Wie das passiert:** Beim Entwickeln probiert man verschiedene Deployment-Methoden aus. Erst systemd, dann Docker. Aber der systemd-Service wurde nie deaktiviert. Und weil sie in verschiedene ES-Indices schreiben (`keepa-deals` vs `keeper-deals` — Tippfehler!), gab es keinen offensichtlichen Konflikt.

**Analogie:** Du hast zwei Wecker gestellt. Einer klingelt im Wohnzimmer, einer im Schlafzimmer. Du hoerst nur den im Schlafzimmer. Der im Wohnzimmer klingelt seit 3 Monaten ins Leere und verbraucht Strom.

**Warum es wichtig ist:**
- Doppelter API-Token-Verbrauch (beide checken Keepa!)
- Verwirrende Daten (welcher Index ist der richtige?)
- Watchdog ueberwacht den falschen Scheduler

**Merksatz:** Wenn du das Deployment-Modell wechselst, raeume das alte auf. `systemctl disable` ist dein Freund.

---

## 3. Der Watchdog — Drei Fehler auf einmal

Der Watchdog (`~/.local/bin/keepa-watchdog.sh`) laeuft alle 5 Minuten via cron. Er soll pruefen ob alles laeuft. Er hatte DREI Fehler gleichzeitig:

| Was | Falsch | Richtig |
|-----|--------|---------|
| Scheduler-Check | `systemctl is-active keepa-scheduler.service` | `docker ps --filter name=scheduler` |
| Projektverzeichnis | `Input/KeepaProjects...` (alte Kopie) | `KeepaProjects...` (Hauptprojekt) |
| ES-Index | `keepa-deals` (systemd-Index) | `keeper-deals` (Docker-Index) |

**Warum er den 2-Tage-Ausfall nicht gemeldet hat:** Er hat den systemd-Scheduler gecheckt. Der lief einwandfrei. Der Docker-Scheduler war unsichtbar fuer ihn.

**Analogie:** Dein Rauchmelder ist im Keller installiert, aber das Feuer ist im Dachgeschoss. Der Melder sagt "alles gut", weil er die falsche Etage ueberwacht.

**Merksatz:** Ein Watchdog der das Falsche ueberwacht ist gefaehrlicher als kein Watchdog. Du denkst du bist abgesichert, bist es aber nicht.

---

## 4. Token Bucket — Wie Keepa API Rate-Limiting funktioniert

Keepa gibt dir nicht einfach X Anfragen pro Minute. Es benutzt ein **Token Bucket** System:

```
Dein Bucket:
  Kapazitaet:  ~1000 Tokens (variiert)
  Refill:      20 Tokens pro Minute
  Tagesbudget: 20 * 60 * 24 = 28.800 Tokens

Kosten pro API-Call:
  Product Query:    15 Tokens  (Preis eines ASINs checken)
  Deal Search:       5 Tokens  (Deals in einer Kategorie suchen)
  Bestsellers:       3 Tokens  (Top-Seller einer Kategorie)
```

**Analogie:** Stell dir ein Prepaid-Handy vor. Du hast 1000 Einheiten. Jede Minute kommen 20 dazu (Flatrate-Refill). Telefonieren kostet 15 Einheiten, SMS 5. Wenn du schneller verbrauchst als nachgeladen wird, musst du warten.

**Dein aktueller Verbrauch:**
```
31 ASINs × 15 Tokens × 4 Checks/Tag = 1.860 Tokens/Tag
Budget: 28.800 Tokens/Tag
Auslastung: ~6.5%
```

**Bei 10.000 ASINs:**
```
10.000 ASINs × 15 Tokens × 4 Checks/Tag = 600.000 Tokens/Tag
Budget: 28.800 Tokens/Tag
Problem: 20x ueber Budget!
```

Dann braucht man:
1. **Batching** — mehrere ASINs pro API-Call (bis zu 100)
2. **Adaptive Intervalle** — stabile Produkte seltener checken
3. **Oder einen teureren Plan** (mehr Tokens/Minute)

**Im Code:**

```python
# keepa_api.py — AsyncTokenBucket
class AsyncTokenBucket:
    tokens_available = 20       # aktuell verfuegbar
    tokens_per_minute = 20      # refill rate
    refill_interval = 60        # alle 60 Sekunden

    async def wait_for_tokens(self, cost):
        # Wenn nicht genug da: warte bis Refill
        while self.tokens_available < cost:
            await asyncio.sleep(check_interval)
        self.tokens_available -= cost
```

**Merksatz:** Token Bucket = Prepaid-Guthaben mit Auto-Aufladung. Plane deinen Verbrauch gegen das Tagesbudget, nicht gegen den momentanen Stand.

---

## 5. Observability: Von "blind" zu "Dashboard in 30 Minuten"

**Vorher:** Token-Verbrauch existierte nur in Container-Logs. Flüchtig, nicht durchsuchbar, kein Trend sichtbar.

**Nachher:** Jeder API-Call schreibt ein Metrik-Dokument in Elasticsearch:

```python
# keepa_api.py — nach jedem API-Call
metric = {
    "timestamp":      "2026-02-25T16:13:35",
    "operation":      "query",           # was wurde aufgerufen
    "tokens_consumed": 15,               # was hat es gekostet
    "tokens_left":     737,              # was ist noch uebrig
    "refill_rate":     20,               # wie schnell laden wir nach
    "response_time_ms": 4441,            # wie lange hat Keepa gebraucht
    "domain":          "DE",             # welcher Amazon-Marktplatz
    "success":         True,             # hat es funktioniert
}
await es.index_token_metric(metric)
```

**Die Architektur:**
```
Keepa API Call
    |
    v
keepa_api.py / keepa_client.py
    |                    |
    v                    v
Response              Metric-Doc
    |                    |
    v                    v
Scheduler            Elasticsearch
                     (keeper-metrics Index)
                         |
                         v
                    Kibana Dashboard
                    - Tokens Left (Area Chart)
                    - Consumed/Hour (Bar Chart)
                    - Cost by Operation (Pie)
                    - Response Time (Line)
```

**Warum Lazy Import?**
```python
def _get_es_service(cls):
    if cls._es_service_ref is None:
        from src.services.elasticsearch_service import es_service
        cls._es_service_ref = es_service
    return cls._es_service_ref
```

`keepa_api.py` und `elasticsearch_service.py` kennen sich nicht direkt. Der Lazy Import vermeidet zirkulaere Abhaengigkeiten (A importiert B importiert A → Crash). Stattdessen: beim ersten Aufruf importieren, dann cachen.

**Warum `try/except pass` beim Metrik-Schreiben?**
```python
try:
    await es.index_token_metric(metric)
except Exception:
    pass   # Metriken sind optional — API-Call darf nicht wegen Monitoring fehlschlagen
```

Monitoring darf den ueberwachten Prozess nie kaputtmachen. Wenn ES kurz nicht erreichbar ist, verlieren wir ein paar Metriken — aber der Price-Check laeuft weiter. **Observability ist sekundaer, Funktion ist primaer.**

**Merksatz:** Observability = Daten die du schon hast (Token-Verbrauch in API-Responses) in ein System schreiben, das Trends sichtbar macht (ES + Kibana). Kein neues Tool noetig, nur eine Bruecke zwischen bestehenden Systemen.

---

## Zusammenfassung: 5 Lektionen

| # | Lektion | Einzeiler |
|---|---------|-----------|
| 1 | Race Condition | Services starten nicht gleichzeitig — immer Reconnect-Logik einbauen |
| 2 | Zombie-Deployments | Altes Deployment aufräumen wenn du das Modell wechselst |
| 3 | Watchdog-Falle | Ein Watchdog der das Falsche checkt ist schlimmer als keiner |
| 4 | Token Budget | Denk in Tagesbudget (28.800), nicht in aktuellem Stand (709) |
| 5 | Observability | Daten die du schon hast → ES → Kibana. Keine Raketenwissenschaft |
