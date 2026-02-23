"""
Tests for Deals endpoints.
Tests the /api/v1/deals/* routes.
"""

import pytest
from unittest.mock import AsyncMock, patch
from src.services.keepa_api import TokenLimitError, NoDealAccessError


def _deal_payload():
    return {
        "categories": ["16142011"],
        "min_discount": 20,
        "max_discount": 80,
        "min_price": 0,
        "max_price": 500,
        "min_rating": 4.0,
    }


@pytest.mark.asyncio
async def test_search_deals_success(client):
    """POST /api/v1/deals/search returns 200 with list"""
    with patch("src.api.main.deal_finder.search_deals", new_callable=AsyncMock) as mock:
        mock.return_value = [
            {
                "asin": "B09V3KXJPB",
                "title": "Test Product",
                "current_price": 199.99,
                "list_price": 299.99,
                "discount_percent": 33.3,
                "rating": 4.5,
                "reviews": 100,
                "prime_eligible": True,
                "url": "https://amazon.de/dp/B09V3KXJPB",
                "deal_score": 72.3,
                "source": "product_api",
            }
        ]
        response = await client.post("/api/v1/deals/search", json=_deal_payload())

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["source"] == "product_api"


@pytest.mark.asyncio
async def test_search_deals_empty_list(client):
    """POST /api/v1/deals/search returns empty list when no deals"""
    with patch("src.api.main.deal_finder.search_deals", new_callable=AsyncMock) as mock:
        mock.return_value = []
        response = await client.post("/api/v1/deals/search", json=_deal_payload())

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_search_deals_token_limit(client):
    """POST /api/v1/deals/search returns 429 when token limit hit"""
    with patch("src.api.main.deal_finder.search_deals", new_callable=AsyncMock) as mock:
        mock.side_effect = TokenLimitError("Token bucket depleted")
        response = await client.post("/api/v1/deals/search", json=_deal_payload())

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_search_deals_no_access_uses_fallback(client):
    """
    If deal access is unavailable, endpoint retries with product-only fallback
    and still returns 200.
    """
    with patch("src.api.main.deal_finder.search_deals", new_callable=AsyncMock) as mock:
        mock.side_effect = [NoDealAccessError("Deal API access required"), []]
        response = await client.post("/api/v1/deals/search", json=_deal_payload())

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_search_deals_with_query_params(client):
    """POST /api/v1/deals/search with filters returns 200"""
    with patch("src.api.main.deal_finder.search_deals", new_callable=AsyncMock) as mock:
        mock.return_value = [
            {
                "asin": "B09V3KXJPB",
                "title": "Filtered Product",
                "current_price": 99.99,
                "list_price": 199.99,
                "discount_percent": 50.0,
                "rating": 4.5,
                "reviews": 100,
                "prime_eligible": True,
                "url": "https://amazon.de/dp/B09V3KXJPB",
                "deal_score": 85.0,
                "source": "product_api",
            }
        ]

        response = await client.post(
            "/api/v1/deals/search",
            json={
                "categories": ["16142011"],
                "min_discount": 30,
                "max_discount": 70,
                "min_price": 50,
                "max_price": 200,
                "min_rating": 4.5,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
