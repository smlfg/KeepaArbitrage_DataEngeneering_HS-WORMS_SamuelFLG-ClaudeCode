#!/bin/bash
# Job 4: Kafka Pipeline Flow
# Ueberwacht die Kafka Topics: wie viele Messages fliessen durch?
# Zeigt ob Producer UND Consumer arbeiten (wichtig fuer Data Engineering Note!)
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === KAFKA FLOW ==="

# Check if Kafka is running
KAFKA_UP=$(docker-compose ps kafka 2>/dev/null | grep -c "Up\|running" || echo 0)
echo "Kafka running: $([ "$KAFKA_UP" -gt 0 ] && echo 'JA' || echo 'NEIN')"

# List topics
echo "--- Topics ---"
docker-compose exec -T kafka kafka-topics --bootstrap-server localhost:29092 --list 2>/dev/null | grep -E "price|deal" || echo "Keine relevanten Topics"

# Message counts per topic
for TOPIC in price-updates deal-updates; do
    COUNT=$(docker-compose exec -T kafka kafka-run-class kafka.tools.GetOffsetShell \
        --broker-list localhost:29092 --topic "$TOPIC" --time -1 2>/dev/null | \
        awk -F: '{sum += $3} END {print sum}' 2>/dev/null || echo "N/A")
    echo "Topic '$TOPIC': $COUNT messages total"
done

# Consumer lag
echo "--- Consumer Groups ---"
docker-compose exec -T kafka kafka-consumer-groups --bootstrap-server localhost:29092 --list 2>/dev/null | head -5

echo ""
