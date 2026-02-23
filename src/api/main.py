"""
Keeper System API - Main FastAPI Application
With PostgreSQL persistence, Elasticsearch search, and real-time price monitoring
"""

import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import select as sa_select

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

from src.services.database import (
    init_db,
    create_watch as db_create_watch,
    get_user_watches,
    soft_delete_watch,
    get_active_watch_count,
    async_session_maker,
    PriceHistory,
    WatchedProduct,
)
from src.services.keepa_api import TokenLimitError, NoDealAccessError
from src.services.keepa_api import get_keepa_client
from src.services.elasticsearch_service import es_service
from src.agents.deal_finder import deal_finder
from src.scheduler import run_immediate_check, check_single_asin
from src.config import get_settings

settings = get_settings()


# Pydantic Models
class WatchCreateRequest(BaseModel):
    """Request model for creating a new watch"""

    asin: str = Field(
        ...,
        min_length=10,
        max_length=10,
        description="Amazon Product ASIN (10 characters)",
    )
    target_price: float = Field(..., gt=0, description="Target price to trigger alert")


class WatchResponse(BaseModel):
    """Response model for watch data"""

    id: str
    asin: str
    target_price: float
    current_price: Optional[float] = None
    status: str
    last_checked_at: Optional[str] = None
    created_at: str


class WatchDeleteResponse(BaseModel):
    """Response for watch deletion"""

    status: str
    message: str


class DealSearchRequest(BaseModel):
    """Request model for deal search"""

    categories: List[str] = ["16142011"]
    min_discount: int = Field(default=20, ge=0, le=100)
    max_discount: int = Field(default=80, ge=0, le=100)
    min_price: float = Field(default=0, ge=0)
    max_price: float = Field(default=500, ge=0)
    min_rating: float = Field(default=4.0, ge=0, le=5.0)


class DealResponse(BaseModel):
    """Response model for a deal"""

    asin: str
    title: str
    current_price: float
    list_price: float
    discount_percent: float
    rating: float
    reviews: int
    prime_eligible: bool
    url: str
    deal_score: float
    source: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response"""

    status: str
    timestamp: str
    tokens_available: int
    watches_count: int
    elasticsearch: str
    database: str
    kafka: str


class PriceCheckRequest(BaseModel):
    """Request model for manual price check"""

    asin: str = Field(..., min_length=10, max_length=10)


class PriceCheckResponse(BaseModel):
    """Response for manual price check"""

    asin: str
    title: Optional[str] = None
    current_price: float
    list_price: float
    rating: float
    category: str
    timestamp: str


class TriggerCheckResponse(BaseModel):
    """Response for triggering a price check"""

    status: str
    watches_checked: int
    price_changes: int
    alerts_triggered: int
    timestamp: str


# FastAPI App
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    print("🚀 Keeper System starting...")
    await init_db()
    print("✅ Database initialized")

    # Connect to Elasticsearch
    await es_service.connect()
    print("✅ Elasticsearch connected")

    print("✅ Keeper System ready!")
    yield

    # Cleanup
    await es_service.close()
    print("👋 Keeper System shut down")


app = FastAPI(
    title="Keeper System API",
    description="Amazon Price Monitoring & Deal Finder with Persistent Storage",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5601", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health & Status Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint with deep checks for ES, DB, and Kafka"""
    client = get_keepa_client()
    token_status = client.get_token_status()
    watches_count = await get_active_watch_count()

    # Elasticsearch check
    es_status = "error"
    try:
        if es_service.client and await es_service.client.ping():
            es_status = "ok"
    except Exception:
        pass

    # Database check
    db_status = "error"
    try:
        from sqlalchemy import text
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
            db_status = "ok"
    except Exception:
        pass

    # Kafka check
    kafka_status = "error"
    try:
        from aiokafka import AIOKafkaProducer
        probe = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            request_timeout_ms=3000,
        )
        await probe.start()
        await probe.stop()
        kafka_status = "ok"
    except Exception:
        pass

    all_ok = es_status == "ok" and db_status == "ok" and kafka_status == "ok"

    return {
        "status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "tokens_available": token_status.get("tokens_available", 0),
        "watches_count": watches_count,
        "elasticsearch": es_status,
        "database": db_status,
        "kafka": kafka_status,
    }


@app.get("/api/v1/status")
async def get_status():
    """Get detailed system status"""
    client = get_keepa_client()
    token_status = client.get_token_status()
    rate_limit = client.check_rate_limit()

    return {
        "system": "healthy",
        "version": "2.0.0",
        "token_bucket": token_status,
        "rate_limit": rate_limit,
        "timestamp": datetime.utcnow().isoformat(),
    }


# Watch CRUD Endpoints
@app.get("/api/v1/watches", response_model=List[WatchResponse])
async def list_watches(user_id: str = Query(..., description="User ID")):
    """
    List all watches for a user.
    Returns current price status for each watched product.
    """
    watches = await get_user_watches(user_id)

    return [
        {
            "id": str(w.id),
            "asin": w.asin,
            "target_price": w.target_price,
            "current_price": w.current_price,
            "status": w.status.value if hasattr(w.status, "value") else str(w.status),
            "last_checked_at": w.last_checked_at.isoformat()
            if w.last_checked_at
            else None,
            "created_at": w.created_at.isoformat(),
        }
        for w in watches
    ]


@app.post("/api/v1/watches", response_model=WatchResponse, status_code=201)
async def create_watch(
    request: WatchCreateRequest, user_id: str = Query(..., description="User ID")
):
    """
    Create a new price watch for an Amazon product.
    Automatically fetches current price and starts monitoring.
    """
    # Validate ASIN format
    if len(request.asin) != 10:
        raise HTTPException(
            status_code=400, detail="Invalid ASIN format. Must be 10 characters."
        )

    if request.target_price <= 0:
        raise HTTPException(
            status_code=400, detail="Target price must be greater than 0."
        )

    try:
        # Fetch current price from Keepa
        client = get_keepa_client()
        product_data = await client.query_product(request.asin)

        current_price = product_data.get("current_price", 0) if product_data else None

        # Create watch in database
        watch = await db_create_watch(
            user_id=user_id,
            asin=request.asin,
            target_price=request.target_price,
            current_price=current_price,
        )

        return {
            "id": str(watch.id),
            "asin": watch.asin,
            "target_price": watch.target_price,
            "current_price": watch.current_price,
            "status": watch.status.value
            if hasattr(watch.status, "value")
            else str(watch.status),
            "last_checked_at": watch.last_checked_at.isoformat()
            if watch.last_checked_at
            else None,
            "created_at": watch.created_at.isoformat(),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating watch: {str(e)}")


@app.delete("/api/v1/watches/{watch_id}", response_model=WatchDeleteResponse)
async def delete_watch(watch_id: str, user_id: str = Query(..., description="User ID")):
    """Delete a watch (mark as inactive)"""
    try:
        deleted = await soft_delete_watch(watch_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Watch not found")
        return {
            "status": "deleted",
            "message": f"Watch {watch_id} deleted successfully",
        }
    except ValueError:
        # UUID conversion error
        raise HTTPException(status_code=400, detail="Invalid watch_id or user_id")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting watch: {str(e)}")


# Price Check Endpoints
@app.post("/api/v1/price/check", response_model=PriceCheckResponse)
async def check_price(request: PriceCheckRequest):
    """
    Manually check the current price of a product.
    Returns detailed product information.
    """
    try:
        client = get_keepa_client()
        product_data = await client.query_product(request.asin)

        if not product_data:
            raise HTTPException(
                status_code=404, detail=f"Product {request.asin} not found"
            )

        return {
            "asin": request.asin,
            "title": product_data.get("title"),
            "current_price": product_data.get("current_price", 0),
            "list_price": product_data.get("list_price", 0),
            "rating": product_data.get("rating", 0),
            "category": product_data.get("category", ""),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking price: {str(e)}")


@app.post("/api/v1/price/check-all", response_model=TriggerCheckResponse)
async def trigger_price_check_all():
    """
    Trigger an immediate price check for all active watches.
    This will check prices, update database, and trigger alerts if needed.
    """
    try:
        result = await run_immediate_check()

        return {
            "status": "completed",
            "watches_checked": result.get("total", 0),
            "price_changes": result.get("price_changes", 0),
            "alerts_triggered": result.get("alerts_triggered", 0),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error running price check: {str(e)}"
        )


# Deals Endpoints
@app.post("/api/v1/deals/search", response_model=List[DealResponse])
async def search_deals(request: DealSearchRequest):
    """Search for deals matching the specified criteria"""
    try:
        filter_config = {
            "categories": request.categories,
            "min_discount": request.min_discount,
            "max_discount": request.max_discount,
            "min_price": request.min_price,
            "max_price": request.max_price,
            "min_rating": request.min_rating,
        }
        deals = await deal_finder.search_deals(filter_config)
        return deals[:15]

    except TokenLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except NoDealAccessError as e:
        # Product-only mode can still run even if Keepa deal endpoint is unavailable.
        fallback_config = {
            "categories": request.categories,
            "min_discount": request.min_discount,
            "max_discount": request.max_discount,
            "min_price": request.min_price,
            "max_price": request.max_price,
            "min_rating": request.min_rating,
        }
        try:
            return await deal_finder.search_deals(fallback_config)
        except Exception:
            raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching deals: {str(e)}")


# Elasticsearch-based Deal Search Endpoint
class ElasticsearchDealSearchRequest(BaseModel):
    """Request model for Elasticsearch-based deal search"""

    query: Optional[str] = Field(None, description="Search query for title/description")
    min_discount: float = Field(
        default=0, ge=0, le=100, description="Minimum discount percent"
    )
    max_discount: float = Field(
        default=100, ge=0, le=100, description="Maximum discount percent"
    )
    min_price: Optional[float] = Field(None, ge=0, description="Minimum price")
    max_price: Optional[float] = Field(None, ge=0, description="Maximum price")
    min_rating: float = Field(default=0, ge=0, le=5, description="Minimum rating")
    domain: Optional[str] = Field(None, description="Domain filter (e.g., 'amazon.de')")
    category: Optional[str] = Field(None, description="Category filter")
    page: int = Field(default=0, ge=0, description="Page number")
    size: int = Field(default=15, ge=1, le=100, description="Results per page")


class ElasticsearchDealResponse(BaseModel):
    """Response model for Elasticsearch deal search"""

    total: int
    deals: List[Dict[str, Any]]
    aggregations: Optional[Dict[str, Any]] = None


@app.post("/api/v1/deals/es-search", response_model=ElasticsearchDealResponse)
async def search_deals_elasticsearch(request: ElasticsearchDealSearchRequest):
    """
    Search deals using Elasticsearch.
    Supports full-text search, filters, fuzzy matching, and aggregations.
    """
    try:
        # Build the Elasticsearch query
        must_clauses = []
        filter_clauses = []

        # Full-text search on title and description
        if request.query:
            must_clauses.append(
                {
                    "multi_match": {
                        "query": request.query,
                        "fields": ["title^3", "description", "title.keyword"],
                        "fuzziness": "AUTO",
                        "operator": "or",
                    }
                }
            )

        # Discount range filter
        filter_clauses.append(
            {
                "range": {
                    "discount_percent": {
                        "gte": request.min_discount,
                        "lte": request.max_discount,
                    }
                }
            }
        )

        # Price range filter
        if request.min_price or request.max_price:
            price_range = {"range": {"current_price": {}}}
            if request.min_price:
                price_range["range"]["current_price"]["gte"] = request.min_price
            if request.max_price:
                price_range["range"]["current_price"]["lte"] = request.max_price
            filter_clauses.append(price_range)

        # Rating filter
        if request.min_rating > 0:
            filter_clauses.append({"range": {"rating": {"gte": request.min_rating}}})

        # Domain filter
        if request.domain:
            filter_clauses.append({"term": {"domain": request.domain}})

        # Category filter
        if request.category:
            filter_clauses.append({"term": {"category": request.category}})

        # Build final query
        if must_clauses or filter_clauses:
            query = {"bool": {}}
            if must_clauses:
                query["bool"]["must"] = must_clauses
            if filter_clauses:
                query["bool"]["filter"] = filter_clauses
        else:
            query = {"match_all": {}}

        # Check if ES client is available
        if not es_service.client:
            raise HTTPException(status_code=503, detail="Elasticsearch not available")

        # Execute search with aggregations
        result = await es_service.client.search(
            index=settings.elasticsearch_index_deals,
            query=query,
            from_=request.page * request.size,
            size=request.size,
            sort=[{"deal_score": "desc"}, {"timestamp": "desc"}],
            aggs={
                "by_category": {"terms": {"field": "category", "size": 20}},
                "by_domain": {"terms": {"field": "domain", "size": 10}},
                "price_stats": {"stats": {"field": "current_price"}},
                "discount_stats": {"stats": {"field": "discount_percent"}},
                "rating_stats": {"stats": {"field": "rating"}},
                "top_deals_by_discount": {
                    "terms": {"field": "discount_percent", "size": 10}
                },
            },
        )

        # Parse results
        hits = result.get("hits", {})
        total = hits.get("total", {}).get("value", 0)

        deals = []
        for hit in hits.get("hits", []):
            deal = hit.get("_source", {})
            deal["_score"] = hit.get("_score")
            deals.append(deal)

        aggregations = result.get("aggregations", {})

        return {"total": total, "deals": deals, "aggregations": aggregations}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error searching deals in Elasticsearch: {str(e)}"
        )


@app.get("/api/v1/deals/aggregations")
async def get_deal_aggregations(
    min_discount: float = Query(default=0, ge=0, le=100),
    min_rating: float = Query(default=0, ge=0, le=5),
    domain: Optional[str] = Query(default=None),
):
    """
    Get aggregated deal statistics from Elasticsearch.
    Useful for dashboards and analytics.
    """
    try:
        aggregations = await es_service.get_deal_aggregations(
            min_discount=min_discount, min_rating=min_rating, domain=domain
        )
        return aggregations
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting aggregations: {str(e)}"
        )


@app.post("/api/v1/deals/index")
async def index_deal_to_elasticsearch(deal: Dict[str, Any]):
    """
    Index a deal document to Elasticsearch.
    Used by the DealFinderAgent to store deals for search.
    """
    try:
        # Transform deal data to match ES mapping
        es_doc = {
            "asin": deal.get("asin", ""),
            "title": deal.get("title", ""),
            "description": deal.get("description", ""),
            "current_price": deal.get("current_price", 0),
            "original_price": deal.get("list_price", 0),
            "discount_percent": deal.get("discount_percent", 0),
            "rating": deal.get("rating", 0),
            "review_count": deal.get("reviews", 0),
            "sales_rank": deal.get("sales_rank", 0),
            "domain": deal.get("domain", "amazon.de"),
            "category": deal.get("category", "general"),
            "prime_eligible": deal.get("prime_eligible", False),
            "url": deal.get("url", ""),
            "deal_score": deal.get("deal_score", 0),
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "deal_indexed",
        }

        success = await es_service.index_deal_update(es_doc)

        if success:
            return {"status": "indexed", "asin": es_doc["asin"]}
        else:
            raise HTTPException(status_code=500, detail="Failed to index deal")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error indexing deal: {str(e)}")


# Price History Endpoints
@app.get("/api/v1/prices/{asin}/history")
async def get_price_history(
    asin: str,
    days: int = Query(
        default=30, ge=1, le=365, description="Number of days to look back"
    ),
):
    """Get price history for an ASIN from deal snapshots."""
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session_maker() as session:
        result = await session.execute(
            sa_select(PriceHistory, WatchedProduct)
            .join(WatchedProduct, PriceHistory.watch_id == WatchedProduct.id)
            .where(
                WatchedProduct.asin == asin,
                PriceHistory.recorded_at >= cutoff,
            )
            .order_by(PriceHistory.recorded_at)
        )
        rows = result.all()

    if not rows:
        return {"asin": asin, "history": [], "count": 0}

    history = [
        {
            "price": ph.price,
            "recorded_at": ph.recorded_at.isoformat() if ph.recorded_at else None,
            "source": ph.buy_box_seller,
        }
        for ph, wp in rows
    ]

    return {
        "asin": asin,
        "history": history,
        "count": len(history),
        "period_days": days,
    }


@app.get("/api/v1/prices/{asin}/stats")
async def get_price_stats(asin: str):
    """Get price statistics for an ASIN from Elasticsearch deal data."""
    try:
        stats = await es_service.get_deal_price_stats(asin)
        if not stats or stats.get("data_points", 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No price data found for ASIN {asin}",
            )
        return {"asin": asin, **stats}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting price stats: {str(e)}",
        )


# Rate Limit Endpoints
@app.get("/api/v1/tokens")
async def get_token_status():
    """Get current token bucket status"""
    client = get_keepa_client()
    return client.get_token_status()


@app.get("/api/v1/rate-limit")
async def get_rate_limit():
    """Get rate limit information from Keepa API"""
    client = get_keepa_client()
    return client.check_rate_limit()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
