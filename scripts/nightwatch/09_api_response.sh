#!/bin/bash
# Job 9: API Response Time & Health
# Misst die Antwortzeiten der FastAPI Endpoints
# Zeigt ob die API fuer den Prof-Demo flott genug ist
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === API RESPONSE ==="

API_BASE="http://localhost:8000"

# Health endpoint
echo "--- /health ---"
HEALTH_TIME=$( { time curl -s -o /dev/null -w "%{http_code}" "$API_BASE/health" ; } 2>&1 )
echo "$HEALTH_TIME"

# Deals endpoint
echo "--- /deals (Top Deals) ---"
DEALS_RESP=$(curl -s -w "\nHTTP %{http_code} in %{time_total}s" "$API_BASE/deals?limit=5" 2>/dev/null)
echo "$DEALS_RESP" | tail -1

# Watches endpoint
echo "--- /watches ---"
WATCHES_RESP=$(curl -s -w "\nHTTP %{http_code} in %{time_total}s" "$API_BASE/watches" 2>/dev/null)
echo "$WATCHES_RESP" | tail -1

# Search endpoint (if exists)
echo "--- /search?q=keyboard ---"
SEARCH_RESP=$(curl -s -w "\nHTTP %{http_code} in %{time_total}s" "$API_BASE/search?q=keyboard" 2>/dev/null)
echo "$SEARCH_RESP" | tail -1

# Count active watches
echo "--- Aktive Watches ---"
curl -s "$API_BASE/watches" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        print(f'{len(data)} aktive Watches')
    elif isinstance(data, dict) and 'watches' in data:
        print(f\"{len(data['watches'])} aktive Watches\")
    else:
        print(f'Response: {str(data)[:100]}')
except:
    print('API nicht erreichbar')
"

echo ""
