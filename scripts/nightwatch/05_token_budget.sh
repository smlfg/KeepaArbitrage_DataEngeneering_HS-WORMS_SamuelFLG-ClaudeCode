#!/bin/bash
# Job 5: Keepa Token Budget Tracker
# Ueberwacht den Keepa API Token-Verbrauch
# Wichtig: Keepa hat ein Token-Limit, wenn leer = keine Daten mehr
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === TOKEN BUDGET ==="

# Query the API health endpoint
TOKEN_INFO=$(curl -s "http://localhost:8000/health" 2>/dev/null)

if [ -n "$TOKEN_INFO" ]; then
    echo "$TOKEN_INFO" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f\"Tokens available: {d.get('tokens_available', '?')}\")
    print(f\"Watches: {d.get('watches_count', '?')}\")
    print(f\"Status: {d.get('status', '?')}\")
    print(f\"Elasticsearch: {d.get('elasticsearch', '?')}\")
    print(f\"Database: {d.get('database', '?')}\")
    print(f\"Kafka: {d.get('kafka', '?')}\")
except Exception as e:
    print(f'Parse error: {e}')
"
else
    echo "API nicht erreichbar (localhost:8000)"
fi

# Check logs for token-related warnings
TOKEN_WARNS=$(docker-compose logs --since 20m app 2>/dev/null | grep -ci "token\|rate.limit\|429" || true)
TOKEN_WARNS=${TOKEN_WARNS:-0}
echo "Token/Rate-Limit Warnings (20 Min): $TOKEN_WARNS"

echo ""
