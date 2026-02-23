#!/bin/bash
# Nightly backup: PostgreSQL dump + ES index snapshots
# Usage: bash scripts/backup.sh
# Cron:  0 3 * * * /home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge/scripts/backup.sh >> /var/log/keeper_backup.log 2>&1

set -euo pipefail

BACKUP_DIR="/home/smlflg/DataEngeeneeringKEEPA/backups/$(date +%Y%m%d)"
CONTAINER="keepaprojectsfordataengeneering3branchesmerge_db_1"
ES_URL="http://localhost:9200"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"
echo "[$(date)] Starting backup to $BACKUP_DIR"

# PostgreSQL dump via Docker
echo "[$(date)] Dumping PostgreSQL..."
docker exec "$CONTAINER" pg_dump -U postgres keeper | gzip > "$BACKUP_DIR/keeper_pg.sql.gz"
echo "[$(date)] PostgreSQL dump: $(du -sh "$BACKUP_DIR/keeper_pg.sql.gz" | cut -f1)"

# ES index snapshots
for INDEX in keeper-deals keeper-prices; do
    echo "[$(date)] Snapshotting ES index: $INDEX"
    curl -s "$ES_URL/$INDEX/_search?size=10000" | gzip > "$BACKUP_DIR/${INDEX}.json.gz"
    echo "[$(date)] $INDEX snapshot: $(du -sh "$BACKUP_DIR/${INDEX}.json.gz" | cut -f1)"
done

# Retention: delete backups older than N days
BACKUP_ROOT="$(dirname "$BACKUP_DIR")"
find "$BACKUP_ROOT" -maxdepth 1 -type d -mtime +$RETENTION_DAYS -exec rm -rf {} \;

echo "[$(date)] Backup complete: $BACKUP_DIR"
ls -lh "$BACKUP_DIR"
