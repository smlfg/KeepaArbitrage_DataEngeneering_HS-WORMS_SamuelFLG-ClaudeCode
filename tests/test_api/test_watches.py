"""
Tests for Watch CRUD endpoints.
Tests the /api/v1/watches routes.
"""

import pytest
import uuid


@pytest.mark.asyncio
async def test_create_watch_success(client, mock_keepa):
    """POST /api/v1/watches?user_id=xxx returns 201 with valid ASIN + target_price"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "title": "Test Product",
        "current_price": 299.99,
        "list_price": 349.99,
    }

    user_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/watches?user_id=" + user_id,
        json={"asin": "B09V3KXJPB", "target_price": 250.0},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["asin"] == "B09V3KXJPB"
    assert data["target_price"] == 250.0


@pytest.mark.asyncio
async def test_create_watch_response_fields(client, mock_keepa):
    """POST response has expected fields (id, asin, target_price, status)"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "current_price": 299.99,
    }

    user_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/watches?user_id={user_id}",
        json={"asin": "B09V3KXJPB", "target_price": 250.0},
    )

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert "asin" in data
    assert "target_price" in data
    assert "status" in data
    assert "current_price" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_watch_invalid_asin(client):
    """POST with invalid ASIN (e.g. \"INVALID\") returns 400 or 422"""
    user_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/watches?user_id={user_id}",
        json={"asin": "INVALID", "target_price": 250.0},
    )

    assert response.status_code in [400, 422]


@pytest.mark.asyncio
async def test_create_watch_negative_price(client):
    """POST with negative target_price returns 400"""
    user_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/watches?user_id={user_id}",
        json={"asin": "B09V3KXJPB", "target_price": -50.0},
    )

    assert response.status_code in [400, 422]


@pytest.mark.asyncio
async def test_create_watch_without_user_id(client):
    """POST without user_id query param returns 422"""
    response = await client.post(
        "/api/v1/watches",
        json={"asin": "B09V3KXJPB", "target_price": 250.0},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_watches_empty(client):
    """GET /api/v1/watches?user_id=xxx returns 200 with empty list for new user"""
    user_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/watches?user_id={user_id}")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_watches_after_create(client, mock_keepa):
    """GET /api/v1/watches after POST returns list with the created watch"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "current_price": 299.99,
    }

    user_id = str(uuid.uuid4())

    # Create a watch
    create_response = await client.post(
        f"/api/v1/watches?user_id={user_id}",
        json={"asin": "B09V3KXJPB", "target_price": 250.0},
    )
    assert create_response.status_code == 201
    created_watch = create_response.json()

    # List watches
    list_response = await client.get(f"/api/v1/watches?user_id={user_id}")
    assert list_response.status_code == 200
    watches = list_response.json()

    assert len(watches) == 1
    assert watches[0]["id"] == created_watch["id"]
    assert watches[0]["asin"] == "B09V3KXJPB"


@pytest.mark.asyncio
async def test_delete_watch_success(client, mock_keepa):
    """DELETE /api/v1/watches/{id} returns 200 for existing watch"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "current_price": 299.99,
    }

    user_id = str(uuid.uuid4())

    # Create a watch
    create_response = await client.post(
        f"/api/v1/watches?user_id={user_id}",
        json={"asin": "B09V3KXJPB", "target_price": 250.0},
    )
    assert create_response.status_code == 201
    watch_id = create_response.json()["id"]

    # Delete the watch
    delete_response = await client.delete(
        f"/api/v1/watches/{watch_id}?user_id={user_id}"
    )
    assert delete_response.status_code == 200
    data = delete_response.json()
    assert data["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_watch_not_found(client):
    """DELETE /api/v1/watches/{id} for non-existent id returns 404"""
    user_id = str(uuid.uuid4())
    fake_watch_id = str(uuid.uuid4())

    response = await client.delete(f"/api/v1/watches/{fake_watch_id}?user_id={user_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_watch_user_isolation(client, mock_keepa):
    """Watch list respects user isolation (watches for user A not returned for user B)"""
    mock_keepa.query_product.return_value = {
        "asin": "B09V3KXJPB",
        "current_price": 299.99,
    }

    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())

    # Create watch for user A
    response_a = await client.post(
        f"/api/v1/watches?user_id={user_a}",
        json={"asin": "B09V3KXJPB", "target_price": 250.0},
    )
    assert response_a.status_code == 201

    # List watches for user A
    list_a = await client.get(f"/api/v1/watches?user_id={user_a}")
    assert list_a.status_code == 200
    assert len(list_a.json()) == 1

    # List watches for user B (should be empty)
    list_b = await client.get(f"/api/v1/watches?user_id={user_b}")
    assert list_b.status_code == 200
    assert len(list_b.json()) == 0
