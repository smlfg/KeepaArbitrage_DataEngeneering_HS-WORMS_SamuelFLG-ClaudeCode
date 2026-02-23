#!/bin/bash
# Job 7: Docker Container Health
# Prueft alle Container: laufen sie, wie viel RAM/CPU nutzen sie?
# Alle 15 Min — erkennt Crashes und Memory Leaks
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === DOCKER HEALTH ==="

# Container status
echo "--- Container Status ---"
docker-compose ps 2>/dev/null | grep -E "app|db|kafka|zookeeper|elasticsearch"

# Resource usage
echo "--- Resource Usage ---"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" 2>/dev/null | \
  grep -E "app|db|kafka|zookeeper|elastic|NAME"

# Container restart counts
echo "--- Restart Counts ---"
for SVC in app db kafka zookeeper elasticsearch; do
    RESTARTS=$(docker inspect --format='{{.RestartCount}}' "$(docker-compose ps -q $SVC 2>/dev/null)" 2>/dev/null || echo "N/A")
    echo "  $SVC: $RESTARTS restarts"
done

# Disk usage
echo "--- Docker Disk Usage ---"
docker system df 2>/dev/null | head -4

echo ""
