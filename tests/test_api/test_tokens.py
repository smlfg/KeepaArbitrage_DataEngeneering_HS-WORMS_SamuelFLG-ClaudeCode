import pytest


@pytest.mark.asyncio
class TestTokenEndpoints:
    """Tests for token and rate limit endpoints"""

    async def test_tokens_endpoint_returns_200(self, client):
        """GET /api/v1/tokens returns 200 OK"""
        response = await client.get("/api/v1/tokens")
        assert response.status_code == 200

    async def test_tokens_response_contains_token_fields(self, client, mock_keepa):
        """Token status response contains tokens_available field"""
        mock_keepa.get_token_status.return_value = {
            "tokens_available": 1500,
            "refill_rate": 100,
            "refill_in": 30,
        }

        response = await client.get("/api/v1/tokens")

        assert response.status_code == 200
        data = response.json()

        assert "tokens_available" in data
        assert data["tokens_available"] == 1500
        mock_keepa.get_token_status.assert_called_once()

    async def test_rate_limit_endpoint_returns_200(self, client, mock_keepa):
        """GET /api/v1/rate-limit returns 200 OK with rate limit info"""
        mock_keepa.check_rate_limit.return_value = {
            "status": "ok",
            "tokens_available": 2000,
            "limit": 3000,
            "remaining": 2000,
        }

        response = await client.get("/api/v1/rate-limit")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert data["status"] == "ok"
        mock_keepa.check_rate_limit.assert_called_once()
