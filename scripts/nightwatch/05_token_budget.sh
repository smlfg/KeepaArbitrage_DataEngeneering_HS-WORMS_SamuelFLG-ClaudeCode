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
    tokens = d.get('keepa_tokens', d.get('token_status', {}))
    if isinstance(tokens, dict):
        print(f\"Tokens available: {tokens.get('tokens_available', '?')}\")
        print(f\"Tokens/min: {tokens.get('tokens_per_minute', '?')}\")
        print(f\"Total consumed: {tokens.get('total_tokens_consumed', '?')}\")
    else:
        print(f'Token info: {tokens}')
except Exception as e:
    print(f'Parse error: {e}')
"
else
    echo "API nicht erreichbar (localhost:8000)"
fi

# Check logs for token-related warnings
TOKEN_WARNS=$(docker-compose logs --since 20m app 2>/dev/null | grep -ci "token\|rate.limit\|429" || echo 0)
echo "Token/Rate-Limit Warnings (20 Min): $TOKEN_WARNS"

echo ""
