"""
Tests for Price Check endpoints.
Tests the /api/v1/price/* routes.
"""

import pytest
import uuid
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_price_check_success(client, mock_keepa):
    """POST /api/v1/price/check returns 200 when product found"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "title": "Test Product",
        "current_price": 299.99,
        "list_price": 349.99,
        "rating": 4.5,
        "category": "Electronics",
    }

    response = await client.post(
        "/api/v1/price/check",
        json={"asin": "B09V3KXJPB"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["asin"] == "B09V3KXJPB"
    assert data["current_price"] == 299.99


@pytest.mark.asyncio
async def test_price_check_product_not_found(client, mock_keepa):
    """POST /api/v1/price/check with non-existent product returns 404"""
    mock_keepa.query_product.return_value = None

    response = await client.post(
        "/api/v1/price/check",
        json={"asin": "B09V3KXJPB"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_price_check_invalid_asin(client):
    """POST /api/v1/price/check with invalid ASIN returns 400 or 422"""
    response = await client.post(
        "/api/v1/price/check",
        json={"asin": "INVALID"},
    )

    assert response.status_code in [400, 422]


@pytest.mark.asyncio
async def test_price_check_response_fields(client, mock_keepa):
    """Response includes price/asin/title/updated fields"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "title": "Test Product",
        "current_price": 299.99,
        "list_price": 349.99,
        "rating": 4.5,
        "category": "Electronics",
    }

    response = await client.post(
        "/api/v1/price/check",
        json={"asin": "B09V3KXJPB"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "asin" in data
    assert "title" in data
    assert "current_price" in data
    assert "list_price" in data
    assert "rating" in data
    assert "category" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_price_check_all_success(client, mock_keepa):
    """POST /api/v1/price/check-all returns 200 (bulk check)"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "current_price": 299.99,
    }

    response = await client.post("/api/v1/price/check-all")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "watches_checked" in data
    assert "price_changes" in data
    assert "alerts_triggered" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_price_check_all_handles_errors(client, mock_keepa):
    """Bulk check handles keepa errors gracefully"""
    mock_keepa.query_product.side_effect = Exception("Keepa API error")

    # Even with errors, the endpoint should handle them
    response = await client.post("/api/v1/price/check-all")

    # Should either succeed with error info or return 500
    assert response.status_code in [200, 500]
