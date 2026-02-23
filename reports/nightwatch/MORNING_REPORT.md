# Nightwatch Morning Report

> Automatisch generiert von den 10 Nightwatch Cronjobs.
> Lies das hier morgens als Erstes — dann weisst du genau wo das Projekt steht.

**Generiert:** 2026-02-23 07:00:01

---
## 1. System Health

- Container: **7/5** laufen
- API: **Healthy** (HTTP 200)
- Elasticsearch: **green**
- Kafka: **Running**

---
## 2. Deal Collection (Nacht-Ernte)

- Deals gesammelt (letzte 8h): **0**
- Deals gesamt in DB: **159**
- Durchschnittspreis: **** EUR
- Durchschnitts-Rabatt: **%**
- Durchschnitts-Rating: ****/5
- Durchschnitts-Score: ****/100
- Durchschnittspreis: **** EUR
- Durchschnitts-Rabatt: **%**
- Durchschnitts-Rating: ****/5
- Durchschnitts-Score: ****/100

---
## 3. Keyboard Focus Check

- Keine Deals in den letzten 8h — Collector laeuft eventuell nicht

### Verdaechtige Nicht-Keyboard Deals:
```

```

---
## 4. Top 5 Deals (hoechster Score)

```
 asin | titel | preis | rabatt | score 
------+-------+-------+--------+-------
(0 rows)

```

---
## 5. Fehler-Zusammenfassung

- Errors (8h): **0
0**
- Warnings (8h): **0
0**
- Token/Rate-Limit Issues: **0
0**


---
## 6. Kafka Pipeline

- **price-updates**: 314 messages total
- **deal-updates**: 0 messages total

---
## 7. Pruefungs-Readiness

| Komponente | Status |
|---|---|
| FastAPI | OK |
| PostgreSQL (Deals) | OK (159 Deals) |
| Elasticsearch | OK (green) |
| Kafka | OK |
| Keyboard-Filter | NICHT AKTIV |
| Docker Compose | OK (7 Container) |
| Keepa API | Rate-Limit Probleme |

---
## 8. Empfehlungen fuer heute

1. **Keine Deals gesammelt** — Logs checken: `docker-compose logs --tail 50 app`

---
*Nightwatch Report Ende. Guten Morgen!*
