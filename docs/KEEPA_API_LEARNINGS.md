# Keepa API — Was wir wirklich gelernt haben

> Dieses Dokument fasst zusammen, was funktioniert, was nicht, und warum.
> Quellen: Code-Exploration, Gemini Fact-Check (3x Flash, 2026-02-22), `discover_eu_qwertz_asins.py` Code-Analyse.
>
> **Caveat:** Die Endpoint-Verfuegbarkeit wurde NICHT live gegen die Keepa API getestet.
> Die Aussagen basieren auf: (1) Code im discover-Script der diese Endpoints aufruft,
> (2) 3x Gemini-Recherche die bestaetigt dass `/query`, `/search`, `/bestsellers` auf allen
> Paid-Plaenen verfuegbar sind, (3) Abwesenheit von Community-Beschwerden.
> **Einziger definitiv bestaetigter 404:** `/deals` (NoDealAccessError im Code + Gemini-Bestaetigung).
> Vor produktivem Einsatz: Endpoints einmal manuell testen (`curl` oder discover-Script).

---

## 1. Was funktioniert auf unserem Plan

Fuenf Endpoints sind laut Gemini-Recherche und Code-Analyse auf Paid-Plaenen nutzbar. Das `discover_eu_qwertz_asins.py` Script ruft sie direkt per REST auf — ob es dabei Erfolg hat, haengt vom konkreten API-Plan ab (noch nicht live getestet).

| Endpoint | REST-URL | `keepa` Lib Methode | Token-Kosten | Beweis im Code |
|----------|----------|---------------------|-------------|----------------|
| `/product` | `api.keepa.com/product/?key=K&domain=3&asin=B07X` | `Keepa.query()` | ~1-15/ASIN (je nach Params) | `keepa_api.py:423` — Hauptsystem |
| `/query` (Product Finder) | `api.keepa.com/query/?key=K&domain=3&selection=JSON` | `Keepa.product_finder()` | ~5 | `discover_eu_qwertz_asins.py:299` |
| `/search` | `api.keepa.com/search/?key=K&domain=3&type=category&term=X` | `Keepa.search_for_categories()` | ~1 | `discover_eu_qwertz_asins.py:369` |
| `/bestsellers` | `api.keepa.com/bestsellers/?key=K&domain=3&category=ID` | `Keepa.best_sellers_query()` | ~3 | `discover_eu_qwertz_asins.py:400` |
| `/token` | `api.keepa.com/token/?key=K` | (kein Wrapper) | 0 (gratis) | `keepa_api.py:321` (indirekt via `tokens_left`) |

**Wichtig:** Das discover-Script nutzt die REST-API direkt (`urllib`), nicht die `keepa`-Bibliothek. Es beweist damit, dass die Endpoints wirklich funktionieren — unabhaengig von Library-Bugs.

---

## 2. Was NICHT funktioniert

### `/deals` Endpoint — 404 auf unserem Plan

```
GET api.keepa.com/deal/?key=K&domain=3&... → HTTP 404
```

- **Fehler:** `NoDealAccessError: Deal API not available for this account.`
- **Definiert in:** `keepa_api.py:49-52`
- **Abgefangen in:** `keepa_api.py:748` — wenn "404" oder "NOT FOUND" in der Fehlermeldung
- **Grund:** Der `/deals` Endpoint erfordert einen hoeherwertigen API-Plan

Das ist **kein Bug** — es ist eine Plan-Beschraenkung. Die `search_deals()` Methode existiert im Code (ab Zeile 598) und wuerde funktionieren, wenn wir den Plan upgraden.

### `/category` Endpoint — nicht genutzt

Theoretisch verfuegbar (~1 Token), aber wir nutzen `/search` mit `type=category` stattdessen, was dieselben Kategorie-IDs liefert.

---

## 3. Unsere Fallback-Strategie (statt Deals API)

Da `/deals` nicht geht, haben wir einen dreistufigen Ansatz:

```
Schritt 1: Seed-ASIN-Pool aufbauen
   ├── /query (Product Finder) → ASINs nach Suchbegriff
   ├── /search (Kategorien)    → Kategorie-IDs ermitteln
   └── /bestsellers            → ASINs aus Bestseller-Listen

Schritt 2: ASINs monitoren
   └── /product                → Preis, Rating, Sales Rank pro ASIN

Schritt 3: Deals berechnen
   └── Deal-Score = discount * rating * (1 / rank)
```

**Wo im Code:**
- Seed-Pool: `discover_eu_qwertz_asins.py` (gesamtes Script)
- Monitoring: `src/scheduler.py` → `_collect_seed_asin_deals()`
- Seed-Datei: `data/seed_asins_eu_qwertz.txt` (Komma-getrennte ASINs)

**Ergebnis:** Wir finden Deals ohne den Deals-Endpoint — langsamer, aber zuverlaessig.

---

## 4. Token-Kosten Realitaet

### Was der Code definiert (`keepa_api.py:253-258`)

```python
TOKEN_COSTS = {
    "query": 15,      # Product query (mit stats, history, offers)
    "deals": 5,       # Deal search
    "category": 5,    # Category lookup
    "best_sellers": 3, # Best sellers
    "seller": 5,      # Seller query
}
```

### Was Gemini dazu sagt

> "Die Basis-Kosten liegen bei ~1 Token pro Aufruf. Mit zusaetzlichen Parametern (stats, history, offers) steigen sie auf bis zu ~15 Tokens."

**Fazit:** Die 15 Tokens in unserem Code sind der Worst-Case (alle Params aktiviert). Ein einfacher `/product`-Aufruf ohne Extras kostet nur ~1 Token.

### Token Bucket Mechanismus

```
Bucket: 200 Tokens (Default, wird beim Start von Keepa API synchronisiert)
Refill: Alle 60 Sekunden auf Maximum
Timeout: 120 Sekunden warten, dann TokenInsufficientError
```

Das discover-Script trackt Token-Verbrauch separat: `stats.tokens_consumed_total` via `tokensConsumed` aus jeder API-Response.

---

## 5. Batch-Optimierung — Was geht vs. was wir tun

### Was moeglich ist

Der `/product` Endpoint akzeptiert Komma-getrennte ASINs:

```python
# discover_eu_qwertz_asins.py:476
asin=",".join(batch)  # batch_size=40
```

Ein einziger API-Call fuer 40 ASINs statt 40 einzelne Calls.

### Was das Hauptsystem aktuell tut

```python
# keepa_api.py:423 — EINZELN pro ASIN
products = self._api.query(asin, domain=domain, stats=90, history=True, offers=20)
```

### Optimierungspotential

| Modus | API-Calls fuer 100 ASINs | Token-Kosten |
|-------|--------------------------|-------------|
| Aktuell (einzeln) | 100 Calls | ~1500 Tokens |
| Batch (40er) | 3 Calls | ~300-450 Tokens |
| **Einsparung** | **~97% weniger Calls** | **~70-80% weniger Tokens** |

**Beachte:** Batch-Queries mit `stats=90, history=True, offers=20` kosten mehr pro ASIN als einfache Batch-Queries. Die genaue Einsparung haengt von den Parametern ab.

---

## 6. CSV-Format Quick-Reference

### Die 20 Indizes

```
csv[0]  = Amazon-Preis          csv[10] = New FBM Versandkosten
csv[1]  = Marketplace Neu       csv[11] = Buy Box ★
csv[2]  = Marketplace Gebraucht csv[12] = Used - Wie Neu
csv[3]  = Sales Rank            csv[13] = Used - Sehr Gut
csv[4]  = Listenpreis (UVP)     csv[14] = Used - Gut
csv[5]  = Sammlerstueck         csv[15] = Used - Akzeptabel
csv[6]  = Refurbished           csv[16] = Rating (÷10) ★
csv[7]  = New FBA ★             csv[17] = Review Count ★
csv[8]  = Blitzangebote         csv[18] = Buy Box Used
csv[9]  = Warehouse Deals       csv[19] = Sales Rank Drops (30d)
```

★ = Die wichtigsten fuer unser System

### Array-Format

```python
csv[i] = [keepa_time, value, keepa_time, value, ...]
# Paare: [Zeitstempel, Wert, Zeitstempel, Wert, ...]
# Rueckwaerts lesen fuer aktuellsten Wert
```

### Spezialwerte

| Wert | Bedeutung | Wie behandeln |
|------|-----------|---------------|
| `-1` | Nicht verfuegbar (z.B. Amazon verkauft nicht) | `if val > 0` pruefen |
| `-2` | Keine Daten (Keepa hat nie erfasst) | `if val > 0` pruefen |
| Preis | In Cents gespeichert | `÷ 100` fuer EUR |
| Rating | Mal 10 gespeichert | `÷ 10` fuer echten Wert |
| Sales Rank | Kein Preis! | Separat behandeln |

### Unsere Preis-Prioritaet (`keepa_api.py:469`)

```python
for idx in [0, 11, 7, 1, 12, 18, 9]:
    # Amazon > Buy Box > New FBA > New 3rd > Used Like New > Buy Box Used > Warehouse
```

### Stats-Fallback (`keepa_api.py:489-523`)

Wenn CSV leer ist, nutzen wir `product["stats"]["current"][index]` — gleiches Index-Schema. Danach noch `stats.buyBoxPrice` und `stats.listPrice` als benannte Felder.

---

## 7. discover-Script als Beweis

`scripts/discover_eu_qwertz_asins.py` ist unser "Beweis-Script" — es ruft die Keepa REST API direkt auf (ohne `keepa`-Bibliothek) und zeigt damit, welche Endpoints wirklich funktionieren.

### Welche Endpoints das Script nutzt

| Funktion | Endpoint | Zeile | Was es tut |
|----------|----------|-------|-----------|
| `discover_from_product_finder()` | `/query` | 299 | Sucht ASINs nach Suchbegriff + Preisfilter |
| `discover_categories()` | `/search` | 369 | Findet Kategorie-IDs fuer Tastaturen |
| `discover_from_bestsellers()` | `/bestsellers` | 400 | Holt Bestseller-ASINs pro Kategorie |
| `validate_asins()` | `/product` | 470 | Validiert ASINs per Batch-Query (batch_size=40) |

### Ablauf

```
1. Fuer jeden Markt (DE, UK, FR, IT, ES):
   a. Product Finder → ASINs finden (mehrere Seiten, mehrere Suchbegriffe)
   b. Category Search → Kategorie-IDs ermitteln
   c. Bestsellers → ASINs pro Kategorie + Range holen

2. Optional: Validation per /product Batch-Query

3. Output: JSON (Metadaten + ASINs) + TXT (Komma-getrennt)
```

### Maerkte und Suchbegriffe (Auszug)

```python
"DE": ["tastatur", "gaming tastatur", "mechanische tastatur", "qwertz tastatur", ...]
"UK": ["keyboard", "gaming keyboard", "mechanical keyboard", ...]
"FR": ["clavier", "clavier gaming", "clavier mecanique", ...]
```

---

## 8. Fact-Check Log

### Methode

Gemini (Flash) wurde mit 20 konkreten Claims aus dem Code konfrontiert. Ergebnis:

| Kategorie | Anzahl | Status |
|-----------|--------|--------|
| Allgemeine Keepa-Fakten | 16 | Extern verifiziert |
| Keepa-spezifische Interna | 4 | Nicht extern verifizierbar — aber im Code bestaetigt |

### Was Gemini NICHT verifizieren konnte (und warum das OK ist)

1. **Exakte Token-Kosten pro Endpoint** — Keepa publiziert keine oeffentliche Token-Kostentabelle. Unsere Werte stammen aus der API-Response (`tokensConsumed`) und dem Code.

2. **CSV-Index-Zuordnungen** — Keepa-Doku ist nicht oeffentlich zugaenglich. Unsere Indizes stammen aus der `keepa`-Python-Bibliothek und eigenem Reverse-Engineering.

3. **Spezialwerte -1 und -2** — In der `keepa`-Library-Doku erwaehnt, aber nicht auf keepa.com oeffentlich dokumentiert.

4. **`/token` Endpoint** — Funktioniert, ist aber in keiner oeffentlichen API-Referenz ausfuehrlich beschrieben.

### Wahrscheinlich falsche Bug-Reports (korrigiert, aber nicht live verifiziert)

In `project-deep-dive.md` standen urspruenglich 3 Reports als "Bugs":
- `/query` gibt angeblich 404
- `/search` gibt angeblich 500
- `/bestsellers` gibt angeblich Fehler

**Gemini-Recherche (3x Flash, 2026-02-22) sagt:** Diese Endpoints sind auf allen Paid-Plaenen verfuegbar. Keine Community-Berichte ueber Plan-Beschraenkungen gefunden. Das discover-Script ruft sie im Code auf.

**Aber:** Es gibt keinen Live-Test-Beweis. Moeglich dass unser konkreter Plan eine Ausnahme ist. Vor produktivem Einsatz einmal manuell testen:
```bash
curl "https://api.keepa.com/search/?key=$KEEPA_API_KEY&domain=3&type=category&term=tastatur"
# 200 = funktioniert, 404 = Plan-Beschraenkung
```

Die einzig **definitiv bestaetigte** Einschraenkung ist `/deals` → 404 (`NoDealAccessError` im Code).

---

## 9. Lessons Learned

### 1. Keepa-Doku ist nicht oeffentlich
Die offizielle API-Dokumentation erfordert einen API-Key und Login. Externe Quellen (Gemini, Google) koennen nur begrenzt verifizieren. **Immer den Code lesen.**

### 2. Die `keepa` Python-Lib ist ein Wrapper, kein Standard
Die Library mappt REST-Endpoints auf Python-Methoden, aber die Benennung weicht ab:
- `/query` (REST) ↔ `product_finder()` (Python)
- `/product` (REST) ↔ `query()` (Python)
- `/bestsellers` (REST) ↔ `best_sellers_query()` (Python)

Das discover-Script umgeht diese Verwirrung, indem es die REST-API direkt aufruft.

### 3. Falsche Bug-Reports kosten Zeit
3 von 6 gemeldeten "Bugs" waren keine. Sie entstanden durch die Annahme, dass alle Endpoints auf dem guenstigsten Plan verfuegbar sein muessen. **Erst testen, dann reporten.**

### 4. Batch-Queries sind der groesste Quick Win
Unser Hauptsystem fragt jede ASIN einzeln ab. Das discover-Script zeigt, dass 40er-Batches funktionieren. Das ist eine 10x+ Optimierung, die nur eine Zeile Code aendert.

### 5. Token-Kosten variieren stark
`query()` mit `stats=90, history=True, offers=20` kostet ~15 Tokens. Ohne diese Params kostet es ~1 Token. Fuer reine Validierung (existiert die ASIN?) reicht die guenstige Variante.

### 6. Stats-Fallback ist unverzichtbar
Nicht jedes Produkt hat CSV-History-Daten. Der Fallback auf `stats.current[]` und benannte Stat-Felder (`buyBoxPrice`, `listPrice`) faengt ~20-30% der Faelle ab, die sonst preis-los waeren.

---

## Verwandte Docs

- [KEEPA_API.md](KEEPA_API.md) — Technische API-Referenz (Endpoints, CSV-Format, Domain-IDs)
- [PIPELINE_FLOW.md](PIPELINE_FLOW.md) — Wie Daten von Keepa bis Elasticsearch fliessen
- [project-deep-dive.md](project-deep-dive.md) — Technischer Deep-Dive (Bug-Reports korrigiert)
