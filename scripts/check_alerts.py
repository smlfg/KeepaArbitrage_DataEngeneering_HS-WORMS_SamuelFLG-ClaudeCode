"""
Alert Checking Script - Checks for anomalies in the Keepa pipeline
"""

import sys
from elasticsearch import Elasticsearch

ES_HOST = "http://localhost:9200"
DEALS_INDEX = "keepa-deals"
ARBITRAGE_INDEX = "keepa-arbitrage"


def get_es_client():
    """Initialize Elasticsearch client."""
    return Elasticsearch([ES_HOST])


def check_zero_price(client):
    """Check for products with current_price == 0 (CRITICAL)."""
    query = {"query": {"term": {"current_price": 0}}}
    try:
        result = client.count(index=DEALS_INDEX, body=query)
        return result["count"]
    except Exception:
        return 0


def check_missing_price(client):
    """Check for documents missing current_price field (ERROR)."""
    query = {"query": {"bool": {"must_not": {"exists": {"field": "current_price"}}}}}
    try:
        result = client.count(index=DEALS_INDEX, body=query)
        return result["count"]
    except Exception:
        return 0


def check_no_discount(client):
    """Check for deals with discount_percent == 0 (WARNING)."""
    query = {"query": {"term": {"discount_percent": 0}}}
    try:
        result = client.count(index=DEALS_INDEX, body=query)
        return result["count"]
    except Exception:
        return 0


def check_rating_zero(client):
    """Check for deals with rating == 0 (WARNING)."""
    query = {"query": {"term": {"rating": 0}}}
    try:
        result = client.count(index=DEALS_INDEX, body=query)
        return result["count"]
    except Exception:
        return 0


def check_arbitrage_opportunities(client):
    """Check for arbitrage opportunities count (INFO)."""
    try:
        result = client.count(index=ARBITRAGE_INDEX, body={"query": {"match_all": {}}})
        return result["count"]
    except Exception:
        return 0


def main():
    """Run all alert checks and print results."""
    client = get_es_client()

    if not client.ping():
        print("❌ Elasticsearch not reachable")
        sys.exit(1)

    alerts = []

    zero_price_count = check_zero_price(client)
    if zero_price_count > 0:
        alerts.append(f"🚨 [CRITICAL] {zero_price_count} products with 0€ price found")

    missing_price_count = check_missing_price(client)
    if missing_price_count > 0:
        alerts.append(
            f"🚨 [ERROR] {missing_price_count} documents missing current_price field"
        )

    no_discount_count = check_no_discount(client)
    if no_discount_count > 0:
        alerts.append(f"🟡 [WARNING] {no_discount_count} deals with 0% discount")

    rating_zero_count = check_rating_zero(client)
    if rating_zero_count > 0:
        alerts.append(f"🟡 [WARNING] {rating_zero_count} deals with 0 rating")

    arbitrage_count = check_arbitrage_opportunities(client)
    if arbitrage_count == 0:
        alerts.append(f"ℹ️ [INFO] No arbitrage opportunities found")
    else:
        alerts.append(f"ℹ️ [INFO] {arbitrage_count} arbitrage opportunities available")

    if alerts:
        for alert in alerts:
            print(alert)
    else:
        print("✅ All systems nominal")


if __name__ == "__main__":
    main()
