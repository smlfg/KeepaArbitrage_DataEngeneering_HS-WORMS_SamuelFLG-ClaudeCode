import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from src.agents.price_monitor import PriceMonitorAgent, price_monitor


@pytest.fixture
def agent():
    return PriceMonitorAgent()


@pytest.fixture
def sample_watch():
    return {"asin": "B08N5WRWNW", "target_price": 100.0, "user_id": "user123"}


class TestPriceMonitorInit:
    def test_init_with_default_settings(self, agent):
        assert agent.BATCH_SIZE == 50
        assert agent.VOLATILE_THRESHOLD == 5.0
        assert agent.STABLE_THRESHOLD == 2.0


class TestFetchPrices:
    @pytest.mark.asyncio
    async def test_fetch_prices_returns_list(self, agent):
        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(
                return_value={"asin": "B08N5WRWNW", "current_price": 89.99}
            )
            mock_get_client.return_value = mock_client

            result = await agent.fetch_prices(["B08N5WRWNW"])

            assert isinstance(result, list)
            assert len(result) == 1
            mock_client.query_product.assert_called_once_with("B08N5WRWNW")

    @pytest.mark.asyncio
    async def test_fetch_prices_skips_none_results(self, agent):
        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(
                side_effect=[None, {"asin": "B123", "current_price": 50.0}]
            )
            mock_get_client.return_value = mock_client

            result = await agent.fetch_prices(["B000", "B123"])

            assert len(result) == 1


class TestCheckPrices:
    @pytest.mark.asyncio
    async def test_check_prices_price_below_target_creates_alert(
        self, agent, sample_watch
    ):
        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(
                return_value={"asin": "B08N5WRWNW", "current_price": 90.0}
            )
            mock_get_client.return_value = mock_client

            result = await agent.check_prices([sample_watch])

            assert result["alerts_triggered"] == 1
            assert len(result["alerts"]) == 1
            assert result["alerts"][0]["alert_triggered"] is True

    @pytest.mark.asyncio
    async def test_check_prices_price_above_target_no_alert(self, agent, sample_watch):
        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(
                return_value={"asin": "B08N5WRWNW", "current_price": 150.0}
            )
            mock_get_client.return_value = mock_client

            result = await agent.check_prices([sample_watch])

            assert result["alerts_triggered"] == 0
            assert len(result["alerts"]) == 0

    @pytest.mark.asyncio
    async def test_check_prices_no_product_data_returns_none(self, agent, sample_watch):
        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(return_value=None)
            mock_get_client.return_value = mock_client

            result = await agent.check_prices([sample_watch])

            assert result["processed"] == 0
            assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_check_prices_handles_api_error_gracefully(self, agent, sample_watch):
        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(side_effect=Exception("API Error"))
            mock_get_client.return_value = mock_client

            result = await agent.check_prices([sample_watch])

            assert result["errors"] == 1
            assert result["processed"] == 0


class TestBatchCheck:
    @pytest.mark.asyncio
    async def test_batch_check_iterates_over_watches(self, agent):
        watches = [{"asin": f"B{i:03d}", "target_price": 100.0} for i in range(55)]

        with patch("src.agents.price_monitor.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.query_product = AsyncMock(return_value={"current_price": 150.0})
            mock_get_client.return_value = mock_client

            result = await agent.batch_check(watches)

            assert result["batches"] == 2
            assert result["processed"] == 55

    @pytest.mark.asyncio
    async def test_batch_check_handles_empty_watch_list(self, agent):
        result = await agent.batch_check([])

        assert result["processed"] == 0
        assert result["alerts"] == []
        assert result["errors"] == 0
        assert result["batches"] == 0


class TestCalculateVolatility:
    @pytest.mark.asyncio
    async def test_calculate_volatility(self, agent):
        result = agent.calculate_volatility(45.99, 52.50)
        assert result == pytest.approx(12.4, abs=0.5)
        assert agent.calculate_volatility(50.0, 50.0) == 0.0
        assert agent.calculate_volatility(50.0, 0) == 0.0

    def test_calculate_volatility_price_increase(self, agent):
        result = agent.calculate_volatility(110.0, 100.0)
        assert result == pytest.approx(10.0, abs=0.01)

    def test_calculate_volatility_price_decrease(self, agent):
        result = agent.calculate_volatility(90.0, 100.0)
        assert result == pytest.approx(10.0, abs=0.01)


class TestDetermineNextCheckInterval:
    @pytest.mark.asyncio
    async def test_determine_check_interval(self, agent):
        assert agent.determine_next_check_interval(6.0) == timedelta(hours=2)
        assert agent.determine_next_check_interval(3.0) == timedelta(hours=4)
        assert agent.determine_next_check_interval(1.0) == timedelta(hours=6)

    def test_determine_interval_volatile(self, agent):
        result = agent.determine_next_check_interval(10.0)
        assert result == timedelta(hours=2)

    def test_determine_interval_stable(self, agent):
        result = agent.determine_next_check_interval(1.0)
        assert result == timedelta(hours=6)


class TestPriceMonitorGlobal:
    def test_price_monitor_instance_exists(self):
        assert price_monitor is not None
        assert isinstance(price_monitor, PriceMonitorAgent)
