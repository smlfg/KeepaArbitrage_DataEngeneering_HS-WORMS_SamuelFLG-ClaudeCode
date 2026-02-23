import pytest


@pytest.mark.asyncio
class TestHealthEndpoints:
    """Tests for health check endpoints"""

    async def test_health_endpoint_returns_200(self, client):
        """GET /health returns 200 OK"""
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_health_response_has_expected_fields(self, client):
        """Health response contains status, timestamp, tokens_available, watches_count"""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "timestamp" in data
        assert "tokens_available" in data
        assert "watches_count" in data
        assert data["status"] in ("healthy", "degraded")

    async def test_status_endpoint_returns_200(self, client):
        """GET /api/v1/status returns 200 OK"""
        response = await client.get("/api/v1/status")
        assert response.status_code == 200

    async def test_status_response_has_expected_fields(self, client):
        """Status response contains system, version, token_bucket, rate_limit, timestamp"""
        response = await client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()

        assert "system" in data
        assert "version" in data
        assert "token_bucket" in data
        assert "rate_limit" in data
        assert "timestamp" in data
        assert data["system"] == "healthy"
        assert data["version"] == "2.0.0"
