#!/bin/bash
# Job 10: Morning Report Generator
# Aggregiert alle Nacht-Logs in einen lesbaren Morgen-Report
# Laeuft um 07:00 — das Erste was du morgens liest!
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
REPORTS="$PROJECT_DIR/reports/nightwatch"
REPORT="$REPORTS/MORNING_REPORT.md"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$REPORTS"

cat > "$REPORT" << 'HEADER'
# Nightwatch Morning Report

> Automatisch generiert von den 10 Nightwatch Cronjobs.
> Lies das hier morgens als Erstes — dann weisst du genau wo das Projekt steht.

HEADER

echo "**Generiert:** $(date '+%Y-%m-%d %H:%M:%S')" >> "$REPORT"
echo "" >> "$REPORT"

# ============================================================
# Section 1: System Health Overview
# ============================================================
echo "---" >> "$REPORT"
echo "## 1. System Health" >> "$REPORT"
echo "" >> "$REPORT"

# Docker containers
CONTAINERS_UP=$(docker-compose ps 2>/dev/null | grep -c "Up\|running" || echo 0)
CONTAINERS_TOTAL=$(docker-compose ps 2>/dev/null | grep -cE "app|db|kafka|zookeeper|elastic" || echo 0)
echo "- Container: **${CONTAINERS_UP}/${CONTAINERS_TOTAL}** laufen" >> "$REPORT"

# API health
API_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/health" 2>/dev/null || echo "000")
if [ "$API_CODE" = "200" ]; then
    echo "- API: **Healthy** (HTTP 200)" >> "$REPORT"
else
    echo "- API: **DOWN** (HTTP $API_CODE)" >> "$REPORT"
fi

# ES health
ES_STATUS=$(curl -s "http://localhost:9200/_cluster/health" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unreachable")
echo "- Elasticsearch: **$ES_STATUS**" >> "$REPORT"

# Kafka
KAFKA_UP=$(docker-compose ps kafka 2>/dev/null | grep -c "Up\|running" || echo 0)
echo "- Kafka: **$([ "$KAFKA_UP" -gt 0 ] && echo 'Running' || echo 'DOWN')**" >> "$REPORT"
echo "" >> "$REPORT"

# ============================================================
# Section 2: Deal Collection (DER WICHTIGSTE TEIL)
# ============================================================
echo "---" >> "$REPORT"
echo "## 2. Deal Collection (Nacht-Ernte)" >> "$REPORT"
echo "" >> "$REPORT"

# Total deals overnight
NIGHT_DEALS=$(docker-compose exec -T db psql -U postgres -d keeper -t -c \
  "SELECT COUNT(*) FROM collected_deals WHERE collected_at > NOW() - INTERVAL '8 hours';" 2>/dev/null | tr -d ' ')
echo "- Deals gesammelt (letzte 8h): **${NIGHT_DEALS:-0}**" >> "$REPORT"

# Total deals all time
TOTAL_DEALS=$(docker-compose exec -T db psql -U postgres -d keeper -t -c \
  "SELECT COUNT(*) FROM collected_deals;" 2>/dev/null | tr -d ' ')
echo "- Deals gesamt in DB: **${TOTAL_DEALS:-0}**" >> "$REPORT"

# Average quality
docker-compose exec -T db psql -U postgres -d keeper -t -c "
  SELECT
    ROUND(AVG(current_price)::numeric, 2),
    ROUND(AVG(discount_percent)::numeric, 1),
    ROUND(AVG(rating)::numeric, 1),
    ROUND(AVG(deal_score)::numeric, 1)
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '8 hours';
" 2>/dev/null | while IFS='|' read -r price discount rating score; do
    echo "- Durchschnittspreis: **$(echo $price | tr -d ' ')** EUR" >> "$REPORT"
    echo "- Durchschnitts-Rabatt: **$(echo $discount | tr -d ' ')%**" >> "$REPORT"
    echo "- Durchschnitts-Rating: **$(echo $rating | tr -d ' ')**/5" >> "$REPORT"
    echo "- Durchschnitts-Score: **$(echo $score | tr -d ' ')**/100" >> "$REPORT"
done
echo "" >> "$REPORT"

# ============================================================
# Section 3: Keyboard Focus Check
# ============================================================
echo "---" >> "$REPORT"
echo "## 3. Keyboard Focus Check" >> "$REPORT"
echo "" >> "$REPORT"

KEYBOARD_COUNT=$(docker-compose exec -T db psql -U postgres -d keeper -t -c "
  SELECT COUNT(*) FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '8 hours'
    AND (
      LOWER(title) LIKE '%keyboard%'
      OR LOWER(title) LIKE '%tastatur%'
      OR LOWER(title) LIKE '%mechanisch%'
      OR LOWER(title) LIKE '%mechanical%'
      OR LOWER(title) LIKE '%cherry%'
      OR LOWER(title) LIKE '%logitech%k%'
      OR LOWER(title) LIKE '%razer%'
      OR LOWER(title) LIKE '%corsair%k%'
      OR LOWER(title) LIKE '%qwertz%'
    );
" 2>/dev/null | tr -d ' ')

if [ -n "$NIGHT_DEALS" ] && [ "$NIGHT_DEALS" -gt 0 ] 2>/dev/null; then
    RATIO=$(echo "scale=0; ${KEYBOARD_COUNT:-0} * 100 / $NIGHT_DEALS" | bc 2>/dev/null || echo "?")
    echo "- Keyboard-Deals: **${KEYBOARD_COUNT:-0}** von ${NIGHT_DEALS} (**${RATIO}%**)" >> "$REPORT"
    if [ "${RATIO:-0}" -ge 80 ] 2>/dev/null; then
        echo "- Status: **SUPER** — Fast nur Keyboards!" >> "$REPORT"
    elif [ "${RATIO:-0}" -ge 50 ] 2>/dev/null; then
        echo "- Status: **OK** — Mehrheit Keyboards, aber noch Rauschen" >> "$REPORT"
    else
        echo "- Status: **PROBLEM** — Zu viele Nicht-Keyboard Deals. Category-Filter pruefen!" >> "$REPORT"
    fi
else
    echo "- Keine Deals in den letzten 8h — Collector laeuft eventuell nicht" >> "$REPORT"
fi
echo "" >> "$REPORT"

# Suspicious non-keyboard deals
echo "### Verdaechtige Nicht-Keyboard Deals:" >> "$REPORT"
echo '```' >> "$REPORT"
docker-compose exec -T db psql -U postgres -d keeper -t -c "
  SELECT LEFT(title, 55) as titel
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '8 hours'
    AND LOWER(title) NOT LIKE '%keyboard%'
    AND LOWER(title) NOT LIKE '%tastatur%'
    AND LOWER(title) NOT LIKE '%mechanisch%'
    AND LOWER(title) NOT LIKE '%mechanical%'
    AND LOWER(title) NOT LIKE '%cherry%'
    AND LOWER(title) NOT LIKE '%logitech%'
    AND LOWER(title) NOT LIKE '%razer%'
    AND LOWER(title) NOT LIKE '%corsair%'
    AND LOWER(title) NOT LIKE '%qwertz%'
  LIMIT 5;
" 2>/dev/null >> "$REPORT" || echo "(keine Daten)" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ============================================================
# Section 4: Top Deals
# ============================================================
echo "---" >> "$REPORT"
echo "## 4. Top 5 Deals (hoechster Score)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
docker-compose exec -T db psql -U postgres -d keeper -c "
  SELECT asin, LEFT(title, 40) as titel, current_price as preis, discount_percent as rabatt, deal_score as score
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '8 hours'
  ORDER BY deal_score DESC NULLS LAST
  LIMIT 5;
" 2>/dev/null >> "$REPORT" || echo "(keine Daten)" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ============================================================
# Section 5: Error Summary
# ============================================================
echo "---" >> "$REPORT"
echo "## 5. Fehler-Zusammenfassung" >> "$REPORT"
echo "" >> "$REPORT"

ERROR_COUNT=$(docker-compose logs --since 8h app 2>/dev/null | grep -ci "error\|exception\|traceback" || echo 0)
WARN_COUNT=$(docker-compose logs --since 8h app 2>/dev/null | grep -ci "warning\|warn" || echo 0)
TOKEN_ISSUES=$(docker-compose logs --since 8h app 2>/dev/null | grep -ci "token\|rate.limit" || echo 0)

echo "- Errors (8h): **$ERROR_COUNT**" >> "$REPORT"
echo "- Warnings (8h): **$WARN_COUNT**" >> "$REPORT"
echo "- Token/Rate-Limit Issues: **$TOKEN_ISSUES**" >> "$REPORT"
echo "" >> "$REPORT"

if [ "$ERROR_COUNT" -gt 0 ] 2>/dev/null; then
    echo "### Letzte 5 Errors:" >> "$REPORT"
    echo '```' >> "$REPORT"
    docker-compose logs --since 8h app 2>/dev/null | grep -i "error\|exception" | tail -5 >> "$REPORT"
    echo '```' >> "$REPORT"
fi
echo "" >> "$REPORT"

# ============================================================
# Section 6: Kafka Pipeline
# ============================================================
echo "---" >> "$REPORT"
echo "## 6. Kafka Pipeline" >> "$REPORT"
echo "" >> "$REPORT"

for TOPIC in price-updates deal-updates; do
    COUNT=$(docker-compose exec -T kafka kafka-run-class kafka.tools.GetOffsetShell \
        --broker-list localhost:29092 --topic "$TOPIC" --time -1 2>/dev/null | \
        awk -F: '{sum += $3} END {print sum}' 2>/dev/null || echo "N/A")
    echo "- **$TOPIC**: $COUNT messages total" >> "$REPORT"
done
echo "" >> "$REPORT"

# ============================================================
# Section 7: Pruefungs-Readiness Checklist
# ============================================================
echo "---" >> "$REPORT"
echo "## 7. Pruefungs-Readiness" >> "$REPORT"
echo "" >> "$REPORT"
echo "| Komponente | Status |" >> "$REPORT"
echo "|---|---|" >> "$REPORT"

# Check each component
[ "$API_CODE" = "200" ] && echo "| FastAPI | OK |" >> "$REPORT" || echo "| FastAPI | FEHLT |" >> "$REPORT"
[ "${TOTAL_DEALS:-0}" -gt 0 ] 2>/dev/null && echo "| PostgreSQL (Deals) | OK ($TOTAL_DEALS Deals) |" >> "$REPORT" || echo "| PostgreSQL (Deals) | LEER |" >> "$REPORT"
[ "$ES_STATUS" = "green" ] || [ "$ES_STATUS" = "yellow" ] && echo "| Elasticsearch | OK ($ES_STATUS) |" >> "$REPORT" || echo "| Elasticsearch | FEHLT |" >> "$REPORT"
[ "$KAFKA_UP" -gt 0 ] && echo "| Kafka | OK |" >> "$REPORT" || echo "| Kafka | FEHLT |" >> "$REPORT"
[ "${KEYBOARD_COUNT:-0}" -gt 0 ] 2>/dev/null && echo "| Keyboard-Filter | OK |" >> "$REPORT" || echo "| Keyboard-Filter | NICHT AKTIV |" >> "$REPORT"
echo "| Docker Compose | OK ($CONTAINERS_UP Container) |" >> "$REPORT"
echo "| Keepa API | $([ "$TOKEN_ISSUES" -lt 5 ] 2>/dev/null && echo 'OK' || echo 'Rate-Limit Probleme') |" >> "$REPORT"
echo "" >> "$REPORT"

# ============================================================
# Section 8: Empfehlungen
# ============================================================
echo "---" >> "$REPORT"
echo "## 8. Empfehlungen fuer heute" >> "$REPORT"
echo "" >> "$REPORT"

RECOMMENDATIONS=0

if [ "$API_CODE" != "200" ]; then
    echo "1. **API ist down** — \`docker-compose restart app\`" >> "$REPORT"
    RECOMMENDATIONS=$((RECOMMENDATIONS + 1))
fi

if [ "${NIGHT_DEALS:-0}" -eq 0 ] 2>/dev/null; then
    echo "$((RECOMMENDATIONS + 1)). **Keine Deals gesammelt** — Logs checken: \`docker-compose logs --tail 50 app\`" >> "$REPORT"
    RECOMMENDATIONS=$((RECOMMENDATIONS + 1))
fi

if [ -n "$RATIO" ] && [ "${RATIO:-0}" -lt 50 ] 2>/dev/null; then
    echo "$((RECOMMENDATIONS + 1)). **Keyboard-Ratio zu niedrig** (${RATIO}%) — Category-Filter in scheduler.py pruefen" >> "$REPORT"
    RECOMMENDATIONS=$((RECOMMENDATIONS + 1))
fi

if [ "$ERROR_COUNT" -gt 20 ] 2>/dev/null; then
    echo "$((RECOMMENDATIONS + 1)). **Viele Errors** ($ERROR_COUNT) — \`docker-compose logs app | grep ERROR | tail -20\`" >> "$REPORT"
    RECOMMENDATIONS=$((RECOMMENDATIONS + 1))
fi

if [ "$RECOMMENDATIONS" -eq 0 ]; then
    echo "Alles sieht gut aus! Keine dringenden Aktionen noetig." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "---" >> "$REPORT"
echo "*Nightwatch Report Ende. Guten Morgen!*" >> "$REPORT"

echo "Morning Report geschrieben: $REPORT"
