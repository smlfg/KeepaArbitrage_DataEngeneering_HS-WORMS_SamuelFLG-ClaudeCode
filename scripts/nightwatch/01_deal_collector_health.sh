#!/bin/bash
# Job 1: Deal Collector Health Check
# Prueft ob der Deal Collector laeuft und neue Deals sammelt
# Laeuft alle 30 Min — zeigt ob die Pipeline lebt oder haengt
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === DEAL COLLECTOR HEALTH ==="

# Check if app container is running
APP_STATUS=$(docker-compose ps --format json app 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('State','unknown'))" 2>/dev/null || echo "unknown")
echo "App Container: $APP_STATUS"

# Check recent logs for deal collector activity
echo "--- Letzte Deal Collector Logs (5 Min) ---"
docker-compose logs --since 5m app 2>/dev/null | grep -i "deal collect" | tail -5

# Check for errors
ERROR_COUNT=$(docker-compose logs --since 30m app 2>/dev/null | grep -ci "error\|exception\|traceback" || echo 0)
echo "Errors (letzte 30 Min): $ERROR_COUNT"

# Check if new deals were collected
RECENT_DEALS=$(docker-compose exec -T db psql -U postgres -d keeper -t -c \
  "SELECT COUNT(*) FROM collected_deals WHERE collected_at > NOW() - INTERVAL '30 minutes';" 2>/dev/null | tr -d ' ')
echo "Neue Deals (letzte 30 Min): ${RECENT_DEALS:-0}"

echo ""
