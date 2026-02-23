#!/bin/bash
# ============================================================
# Nightwatch Cron Setup
# Installiert 10 Cronjobs die ueber Nacht das Projekt beobachten
# und morgens um 7:00 einen Gesamtreport generieren.
#
# Usage: bash scripts/nightwatch/00_setup_crons.sh
# Remove: bash scripts/nightwatch/00_setup_crons.sh --remove
# ============================================================

PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
SCRIPTS="$PROJECT_DIR/scripts/nightwatch"
REPORTS="$PROJECT_DIR/reports/nightwatch"
CRON_TAG="# KEEPA_NIGHTWATCH"

mkdir -p "$REPORTS"

if [[ "$1" == "--remove" ]]; then
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    echo "Alle Nightwatch Cronjobs entfernt."
    exit 0
fi

# Bestehende Nightwatch-Crons entfernen (idempotent)
crontab -l 2>/dev/null | grep -v "$CRON_TAG" > /tmp/crontab_clean.txt

cat >> /tmp/crontab_clean.txt << EOF
# --- KEEPA NIGHTWATCH: 10 Monitoring Jobs ---
# Job 1: Deal Collector Health Check (alle 30 Min, 22:00-06:30)
0,30 22-23,0-6 * * * bash $SCRIPTS/01_deal_collector_health.sh >> $REPORTS/01_health.log 2>&1 $CRON_TAG
# Job 2: Database Deal Quality (jede Stunde)
15 22-23,0-6 * * * bash $SCRIPTS/02_deal_quality.sh >> $REPORTS/02_quality.log 2>&1 $CRON_TAG
# Job 3: Elasticsearch Index Health (alle 45 Min)
*/45 22-23,0-6 * * * bash $SCRIPTS/03_elasticsearch_health.sh >> $REPORTS/03_es_health.log 2>&1 $CRON_TAG
# Job 4: Kafka Pipeline Flow (jede Stunde)
30 22-23,0-6 * * * bash $SCRIPTS/04_kafka_flow.sh >> $REPORTS/04_kafka.log 2>&1 $CRON_TAG
# Job 5: Token Budget Tracker (alle 20 Min)
*/20 22-23,0-6 * * * bash $SCRIPTS/05_token_budget.sh >> $REPORTS/05_tokens.log 2>&1 $CRON_TAG
# Job 6: Code Quality Snapshot (1x um Mitternacht)
0 0 * * * bash $SCRIPTS/06_code_quality.sh >> $REPORTS/06_code.log 2>&1 $CRON_TAG
# Job 7: Docker Container Health (alle 15 Min)
*/15 22-23,0-6 * * * bash $SCRIPTS/07_docker_health.sh >> $REPORTS/07_docker.log 2>&1 $CRON_TAG
# Job 8: Keyboard vs Non-Keyboard Ratio (jede Stunde)
45 22-23,0-6 * * * bash $SCRIPTS/08_keyboard_ratio.sh >> $REPORTS/08_ratio.log 2>&1 $CRON_TAG
# Job 9: API Response Time (alle 30 Min)
10,40 22-23,0-6 * * * bash $SCRIPTS/09_api_response.sh >> $REPORTS/09_api.log 2>&1 $CRON_TAG
# Job 10: Morning Report Generator (07:00)
0 7 * * * bash $SCRIPTS/10_morning_report.sh $CRON_TAG
EOF

crontab /tmp/crontab_clean.txt
rm /tmp/crontab_clean.txt

echo "10 Nightwatch Cronjobs installiert!"
echo "Reports landen in: $REPORTS/"
echo "Morning Report: $REPORTS/MORNING_REPORT.md (ab 07:00)"
echo ""
echo "Entfernen mit: bash $0 --remove"
echo "Pruefen mit:   crontab -l | grep NIGHTWATCH"
