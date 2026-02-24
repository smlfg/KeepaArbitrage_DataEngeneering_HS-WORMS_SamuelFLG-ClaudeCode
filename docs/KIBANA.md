# Kibana — Dein visuelles Fenster in die Daten

Stell dir vor: Elasticsearch ist eine riesige Bibliothek voller Buecher (Dokumente).
Kibana ist der Bibliothekar, der dir hilft, genau das richtige Buch zu finden — mit Suchmaske, Statistiken und huebschen Charts.

**URL:** http://localhost:5601

---

## Die 4 wichtigsten Bereiche

Alles erreichbar ueber das **Menu links** (Hamburger-Icon oben links):

| Bereich | Was | Wann |
|---------|-----|------|
| **Discover** | Daten durchsuchen, filtern, einzelne Dokumente anschauen | "Zeig mir alle Deals mit >40% Rabatt" |
| **Dashboards** | Fertige Visualisierungen (Charts, Tabellen, Metriken) | Ueberblick, Trends erkennen |
| **Dev Tools** | Raw Elasticsearch Queries in JSON | Wenn KQL nicht reicht |
| **Management → Data Views** | Welche Indices Kibana kennt | Einmalig einrichten (ist schon gemacht) |

---

## 1. Discover — Daten durchsuchen

Das ist dein Arbeitstier. Hier verbringst du 80% deiner Kibana-Zeit.

### So gehts

1. Menu links → **Discover**
2. Oben links: **Data View** waehlen → `keeper-deals` oder `keeper-prices`
3. Oben: **KQL-Suchleiste** → Query eingeben (siehe unten)
4. Oben rechts: **Zeitfilter** anpassen (Standard: Last 30 days)
5. Links: **Felder** ein-/ausblenden (klick auf `+` neben Feldnamen)

### Vorgefertigte Saved Searches

Wir haben dir 2 Searches vorbereitet — oeffne Discover und klick auf **Open** (oben):

| Name | Was | Data View |
|------|-----|-----------|
| **High Discount Deals (>40%)** | Alle Deals mit ueber 40% Rabatt, sortiert nach Discount | keeper-deals |
| **Recent Price Drops** | Produkte mit Preissenkungen, groesste zuerst | keeper-prices |

---

## 2. KQL — Die Suchsprache

KQL (Kibana Query Language) ist die Suchleiste oben in Discover und Dashboards.
Keine Angst — es ist einfacher als SQL.

### Grundsyntax

```
feldname: wert
feldname > zahl
feldname < zahl
feld1: wert AND feld2 > zahl
feld1: wert OR feld2: wert
NOT feldname: wert
```

### Praxis-Beispiele mit deinen Daten

**keeper-deals** (Deal-Index):

```
# Alle Deals mit >40% Rabatt
discount_percent > 40

# Deals mit hohem Score
deal_score > 70

# Bestimmte ASIN suchen
asin: B0D1234ABC

# Kombination: Gute Deals in DE
domain: de AND discount_percent > 30 AND deal_score > 50

# Titel-Suche (Volltextsuche, weil "text" Feld)
title: mechanische tastatur

# Alles unter 30 EUR mit gutem Rating
current_price < 30 AND rating > 4
```

**keeper-prices** (Preis-Index):

```
# Preissenkungen finden
price_change_percent < 0

# Grosse Preissenkungen (>10%)
price_change_percent < -10

# Guenstige Produkte
current_price < 20

# Bestimmte ASIN verfolgen
asin: B0D1234ABC

# Kombination: Preissenkungen in DE
domain: de AND price_change_percent < -5
```

### Wildcards

```
# Alle ASINs die mit B0D anfangen
asin: B0D*

# Titel enthaelt "logi" (irgendwo)
title: logi*
```

---

## 3. Dashboards — Was du siehst

Menu links → **Dashboards** → eins auswaehlen.

### Deal Overview (6 Panels + Guide)

| Panel | Typ | Was es zeigt |
|-------|-----|-------------|
| Guide (oben) | Markdown | KQL-Tipps + Quick Reference |
| Total Deals | Metrik | Gesamtzahl aller Deals |
| Average Discount % | Metrik | Durchschnittlicher Rabatt |
| Deals by Domain | Pie Chart | Verteilung nach Laendern |
| Discount Distribution | Bar Chart | Wie viele Deals in welcher Rabatt-Stufe |
| Deals over Time | Line Chart | Zeitverlauf der Deal-Anzahl |
| Top Deals by ASIN | Tabelle | Top 20 ASINs nach Haeufigkeit + Avg/Max Discount |

### Price Monitor (4 Panels + Guide)

| Panel | Typ | Was es zeigt |
|-------|-----|-------------|
| Guide (oben) | Markdown | KQL-Tipps + Quick Reference |
| Total Price Events | Metrik | Gesamtzahl aller Preis-Datenpunkte |
| Price Trend | Line Chart | Durchschnittspreis ueber Zeit |
| Price Events by Domain | Pie Chart | Verteilung nach Laendern |
| Top Products by Price Events | Tabelle | Top 20 ASINs nach Preis-Datenpunkten + Avg Price/Change |

### Keepa Pipeline Monitor (1 Panel)

Vega-Diagramm das den End-to-End Datenfluss zeigt: Keepa API → Scheduler → Kafka → PostgreSQL → Elasticsearch → FastAPI.

### Interaktion

- **Zeitfilter aendern:** Oben rechts → "Last 7 days", "Last 90 days", custom Range
- **Panel vergroessern:** Hover ueber Panel → Maximize-Icon (Doppelpfeil)
- **In Discover springen:** Hover ueber Panel → drei Punkte → "Explore underlying data"
- **Filter setzen:** In Pie Charts auf ein Segment klicken → filtert das ganze Dashboard

---

## 4. Dev Tools — Raw Queries

Menu links → **Dev Tools** (ganz unten bei Management).

Hier kannst du Elasticsearch-Queries direkt ausfuehren. Wie eine SQL-Konsole, aber fuer ES.

### Beispiele zum Reinkopieren

```json
# Alle Indices anzeigen
GET _cat/indices?v

# Letzte 5 Deals
GET keeper-deals/_search
{
  "size": 5,
  "sort": [{"timestamp": "desc"}]
}

# Deals mit >50% Rabatt
GET keeper-deals/_search
{
  "query": {
    "range": {"discount_percent": {"gt": 50}}
  },
  "sort": [{"discount_percent": "desc"}],
  "size": 20
}

# Durchschnittspreis pro Domain
GET keeper-deals/_search
{
  "size": 0,
  "aggs": {
    "by_domain": {
      "terms": {"field": "domain"},
      "aggs": {
        "avg_price": {"avg": {"field": "current_price"}},
        "avg_discount": {"avg": {"field": "discount_percent"}}
      }
    }
  }
}

# Dokumente zaehlen
GET keeper-deals/_count
GET keeper-prices/_count

# Bestimmte ASIN suchen
GET keeper-deals/_search
{
  "query": {"term": {"asin": "B0D1234ABC"}},
  "sort": [{"timestamp": "desc"}]
}
```

---

## 5. Arbitrage-Vorbereitung

Aktuell trackst du nur `domain: de` (100% in den Pie Charts). Fuer Arbitrage brauchst du mehrere Domains.

### Was du brauchst

Sobald die Pipeline mehrere Amazon-Domains tracked (de, fr, it, es, uk):

```
# Gleiche ASIN, verschiedene Preise pro Domain
GET keeper-prices/_search
{
  "size": 0,
  "aggs": {
    "by_asin": {
      "terms": {"field": "asin", "size": 100},
      "aggs": {
        "by_domain": {
          "terms": {"field": "domain"},
          "aggs": {
            "avg_price": {"avg": {"field": "current_price"}}
          }
        }
      }
    }
  }
}
```

### Kibana-Dashboard dafuer

Ein neues Panel "Arbitrage Matrix" koennte so aussehen:
- **Datatable:** `terms(asin)` → Split by `terms(domain)` → `average(current_price)`
- **Alert Rule:** Wenn Preisdifferenz zwischen Domains > 20% → Notification

Das ist ein eigener Task — kommt sobald Multi-Domain-Tracking laeuft.

---

## Troubleshooting

| Problem | Loesung |
|---------|---------|
| "No data" in Panels | Zeitfilter pruefen (oben rechts) — auf "Last 30 days" oder groesser stellen |
| Discover zeigt nichts | Data View checken (oben links) — muss `keeper-deals` oder `keeper-prices` sein |
| Dashboard laedt nicht | `docker-compose logs kibana` pruefen, ggf. `docker-compose restart kibana` |
| Neue Daten erscheinen nicht | ES refresh: `curl -X POST http://localhost:9200/_refresh` |
| "index_not_found" | Pipeline muss mindestens 1x gelaufen sein, damit der Index existiert |

---

## Data Views (schon eingerichtet)

| Data View | Index Pattern | Timestamp Field | Inhalt |
|-----------|---------------|-----------------|--------|
| `keeper-deals` | `keeper-deals` | `timestamp` | Deal-Snapshots mit Scoring |
| `keeper-prices` | `keeper-prices` | `timestamp` | Preis-Updates pro ASIN |

Beide werden automatisch per `setup-kibana.sh` erstellt. Musst du nicht manuell anlegen.

---

**Naechster Schritt:** Oeffne http://localhost:5601 → Discover → Data View `keeper-deals` → tippe `discount_percent > 40` ein → Enter. Das ist dein erster KQL-Query. Willkommen bei Kibana.
