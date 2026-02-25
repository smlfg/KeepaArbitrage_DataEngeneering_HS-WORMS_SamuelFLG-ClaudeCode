# Nightwatch Morning Report

> Automatisch generiert von den 10 Nightwatch Cronjobs.
> Lies das hier morgens als Erstes — dann weisst du genau wo das Projekt steht.

**Generiert:** 2026-02-25 16:59:53

---
## 1. System Health

- Container: **7/8** laufen
- API: **Healthy** (HTTP 200)
- Elasticsearch: **green**
- Kafka: **Running**

---
## 2. Deal Collection (Nacht-Ernte)

- Deals gesammelt (letzte 8h): **3**
- Deals gesamt in DB: **179**
- Durchschnittspreis: **62.39** EUR
- Durchschnitts-Rabatt: **34.7%**
- Durchschnitts-Rating: **4.3**/5
- Durchschnitts-Score: **54.3**/100

---
## 3. Keyboard Focus Check

- Keyboard-Deals: **3** von 3 (**100%**)
- Status: **SUPER** — Fast nur Keyboards!

### Verdaechtige Nicht-Keyboard Deals:
```

```

---
## 4. Top 5 Deals (hoechster Score)

```
    asin    |                  titel                   | preis | rabatt | score 
------------+------------------------------------------+-------+--------+-------
 B09MDHVXS9 | GLORIOUS Gaming 114x GPBT-Keycaps - Cher | 62.39 |     38 | 55.98
 B09MDHVXS9 | GLORIOUS Gaming 114x GPBT-Keycaps - Cher | 62.39 |     33 | 53.48
 B09MDHVXS9 | GLORIOUS Gaming 114x GPBT-Keycaps - Cher | 62.39 |     33 | 53.48
(3 rows)

```

---
## 5. Fehler-Zusammenfassung

- Errors (8h): **0**
- Warnings (8h): **0**
- Token/Rate-Limit Issues: **0**


---
## 6. Kafka Pipeline

- **price-updates**: 335 messages total
- **deal-updates**: 0 messages total

---
## 7. Pruefungs-Readiness

| Komponente | Status |
|---|---|
| FastAPI | OK |
| PostgreSQL (Deals) | OK (179 Deals) |
| Elasticsearch | OK (green) |
| Kafka | OK |
| Keyboard-Filter | OK |
| Docker Compose | OK (7 Container) |
| Keepa API | OK |

---
## 8. Empfehlungen fuer heute

Alles sieht gut aus! Keine dringenden Aktionen noetig.

---
*Nightwatch Report Ende. Guten Morgen!*
