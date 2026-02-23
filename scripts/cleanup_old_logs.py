#!/usr/bin/env python3
"""
Cleanup script for Elasticsearch log retention.
Deletes documents older than 10 days from keeper-deals and keeper-prices indices.
Can be run via cron (e.g., daily at 2 AM).

Usage:
    python scripts/cleanup_old_logs.py

Cron example:
    0 2 * * * /path/to/venv/bin/python /home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge/scripts/cleanup_old_logs.py >> /var/log/es_cleanup.log 2>&1
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from elasticsearch import Elasticsearch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("cleanup_old_logs")

ES_HOST = "http://localhost:9200"
RETENTION_DAYS = 10
INDICES = {
    "keeper-deals": "timestamp",
    "keeper-prices": "timestamp",
}


def get_cutoff_date() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return cutoff.isoformat()


def cleanup_index(es: Elasticsearch, index: str, date_field: str, cutoff: str) -> int:
    query = {"range": {date_field: {"lt": cutoff}}}

    try:
        response = es.delete_by_query(
            index=index,
            body={"query": query},
            conflicts="proceed",
        )
        deleted = response.get("deleted", 0)
        logger.info(f"[{index}] Deleted {deleted} documents older than {cutoff}")
        return deleted
    except Exception as e:
        logger.error(f"[{index}] Error during cleanup: {e}")
        return 0


def main():
    cutoff = get_cutoff_date()
    logger.info(
        f"Starting Elasticsearch cleanup (retention: {RETENTION_DAYS} days, cutoff: {cutoff})"
    )

    try:
        es = Elasticsearch(ES_HOST)
        if not es.ping():
            logger.error("Cannot connect to Elasticsearch")
            sys.exit(1)

        logger.info(f"Connected to Elasticsearch at {ES_HOST}")

        total_deleted = 0
        for index, date_field in INDICES.items():
            deleted = cleanup_index(es, index, date_field, cutoff)
            total_deleted += deleted

        logger.info(f"Cleanup complete. Total documents deleted: {total_deleted}")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
