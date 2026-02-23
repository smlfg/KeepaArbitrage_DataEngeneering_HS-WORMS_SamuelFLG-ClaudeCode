#!/bin/bash
# Job 3: Elasticsearch Index Health
# Prueft ob ES laeuft, wie viele Docs drin sind, Cluster-Status
# Wichtig fuer die Pruefung: zeigt dass die Such-Pipeline funktioniert
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === ELASTICSEARCH HEALTH ==="

# Cluster health
echo "--- Cluster Status ---"
curl -s "http://localhost:9200/_cluster/health?pretty" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f\"Status: {d.get('status', 'unknown')}  Nodes: {d.get('number_of_nodes', 0)}  Shards: {d.get('active_shards', 0)}\")
except:
    print('ES nicht erreichbar')
"

# Index stats
echo "--- Index Document Counts ---"
curl -s "http://localhost:9200/_cat/indices?v&h=index,docs.count,store.size" 2>/dev/null | grep -E "keeper|deal|price" || echo "Keine relevanten Indices gefunden"

# Recent deal documents
echo "--- Deals indexiert (letzte Stunde) ---"
RECENT=$(curl -s "http://localhost:9200/keeper-deals/_count" -H 'Content-Type: application/json' -d '{
  "query": {
    "range": {
      "timestamp": {
        "gte": "now-1h"
      }
    }
  }
}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('count', 0))" 2>/dev/null || echo 0)
echo "Neue ES-Docs (1h): $RECENT"

echo ""
