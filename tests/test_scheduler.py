"""
Tests for Price Monitor Scheduler
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from datetime import datetime

from src.scheduler import PriceMonitorScheduler, run_immediate_check


@pytest.fixture
def mock_watch():
    """Create a mock watch object"""
    watch = MagicMock()
    watch.id = uuid.uuid4()
    watch.asin = "B08N5WRWNW"
    watch.current_price = 100.0
    watch.target_price = 80.0
    return watch


@pytest.fixture
def mock_user():
    """Create a mock user object"""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.telegram_chat_id = "123456789"
    user.discord_webhook = "https://discord.com/webhook"
    return user


class TestPriceMonitorScheduler:
    """Test suite for PriceMonitorScheduler"""

    def test_scheduler_initializes_with_correct_default_interval(self):
        """Test 1: Scheduler initializes with correct default interval"""
        scheduler = PriceMonitorScheduler()

        assert scheduler.check_interval == 21600  # 6 hours in seconds
        assert scheduler.batch_size == 50
        assert scheduler.running is False
        assert scheduler.keepa_client is not None

    @pytest.mark.asyncio
    async def test_check_single_price_success(self, mock_watch):
        """Test 2: check_single_price calls keepa_client.query_product and returns success"""
        scheduler = PriceMonitorScheduler()

        # Mock the KeepaAPIClient.query_product
        mock_result = {"current_price": 85.0, "buy_box_price": 84.99}
        scheduler.keepa_client.query_product = AsyncMock(return_value=mock_result)

        result = await scheduler.check_single_price(mock_watch)

        scheduler.keepa_client.query_product.assert_called_once_with(mock_watch.asin)
        assert result["success"] is True
        assert result["watch_id"] == str(mock_watch.id)
        assert result["asin"] == mock_watch.asin
        assert result["current_price"] == 85.0
        assert result["buy_box_seller"] == "84.99"
        assert result["previous_price"] == 100.0
        assert result["price_changed"] is True
        assert result["alert_triggered"] is False  # 85 > 80

    @pytest.mark.asyncio
    async def test_check_single_price_no_data(self, mock_watch):
        """Test 3: check_single_price handles None response gracefully"""
        scheduler = PriceMonitorScheduler()

        # Mock the KeepaAPIClient.query_product to return None
        scheduler.keepa_client.query_product = AsyncMock(return_value=None)

        result = await scheduler.check_single_price(mock_watch)

        scheduler.keepa_client.query_product.assert_called_once_with(mock_watch.asin)
        assert result["success"] is False
        assert result["watch_id"] == str(mock_watch.id)
        assert result["asin"] == mock_watch.asin
        assert result["error"] == "No product data returned"

    @pytest.mark.asyncio
    async def test_check_single_price_exception(self, mock_watch):
        """Test 4: check_single_price handles keepa API exception without crashing"""
        scheduler = PriceMonitorScheduler()

        # Mock the KeepaAPIClient.query_product to raise exception
        scheduler.keepa_client.query_product = AsyncMock(
            side_effect=Exception("API Rate Limit Exceeded")
        )

        result = await scheduler.check_single_price(mock_watch)

        scheduler.keepa_client.query_product.assert_called_once_with(mock_watch.asin)
        assert result["success"] is False
        assert result["watch_id"] == str(mock_watch.id)
        assert result["asin"] == mock_watch.asin
        assert "API Rate Limit Exceeded" in result["error"]

    @pytest.mark.asyncio
    @patch("src.scheduler.get_active_watches")
    @patch("src.scheduler.update_watch_price")
    async def test_run_price_check_updates_watch_price_in_db(
        self, mock_update_watch_price, mock_get_active_watches, mock_watch
    ):
        """Test 5: run_price_check updates watch price in DB"""
        scheduler = PriceMonitorScheduler()

        # Mock database responses
        mock_get_active_watches.return_value = [mock_watch]
        mock_update_watch_price.return_value = None

        # Mock KeepaAPIClient
        mock_result = {"current_price": 75.0, "buy_box_price": 74.99}
        scheduler.keepa_client.query_product = AsyncMock(return_value=mock_result)

        with patch("src.scheduler.create_price_alert", new_callable=AsyncMock):
            with patch(
                "src.scheduler.get_pending_alerts_with_context", new_callable=AsyncMock
            ) as mock_pending:
                mock_pending.return_value = []
                result = await scheduler.run_price_check()

        # Verify database update was called
        mock_update_watch_price.assert_called_once_with(str(mock_watch.id), 75.0, "74.99")
        assert result["successful"] == 1
        assert result["price_changes"] == 1

    @pytest.mark.asyncio
    @patch("src.scheduler.get_active_watches")
    @patch("src.scheduler.update_watch_price")
    @patch("src.scheduler.create_price_alert")
    async def test_run_price_check_creates_price_alert_when_price_drops(
        self,
        mock_create_price_alert,
        mock_update_watch_price,
        mock_get_active_watches,
        mock_watch,
    ):
        """Test 6: run_price_check creates price alert when price drops below target"""
        scheduler = PriceMonitorScheduler()

        # Mock database responses
        mock_get_active_watches.return_value = [mock_watch]
        mock_update_watch_price.return_value = None

        # Mock alert creation
        mock_alert = MagicMock()
        mock_alert.id = uuid.uuid4()
        mock_create_price_alert.return_value = mock_alert

        # Mock KeepaAPIClient - price drops below target (75 < 80)
        mock_result = {"current_price": 75.0, "buy_box_price": 74.99}
        scheduler.keepa_client.query_product = AsyncMock(return_value=mock_result)

        with patch(
            "src.scheduler.get_pending_alerts_with_context", new_callable=AsyncMock
        ) as mock_pending:
            mock_pending.return_value = []
            result = await scheduler.run_price_check()

        # Verify alert was created
        mock_create_price_alert.assert_called_once_with(
            str(mock_watch.id), 75.0, mock_watch.target_price
        )
        assert result["alerts_triggered"] == 1

    @pytest.mark.asyncio
    @patch("src.scheduler.get_active_watches")
    @patch("src.scheduler.update_watch_price")
    async def test_run_price_check_returns_summary(
        self, mock_update_watch_price, mock_get_active_watches, mock_watch
    ):
        """Test 7: run_price_check returns proper summary with counts"""
        scheduler = PriceMonitorScheduler()

        # Create multiple watches with different outcomes
        watch2 = MagicMock()
        watch2.id = uuid.uuid4()
        watch2.asin = "B09G9FPHY6"
        watch2.current_price = 200.0
        watch2.target_price = 150.0

        mock_get_active_watches.return_value = [mock_watch, watch2]
        mock_update_watch_price.return_value = None

        # Mock KeepaAPIClient with different responses
        async def mock_query_product(asin):
            if asin == mock_watch.asin:
                return {
                    "current_price": 75.0,
                    "buy_box_price": 74.99,
                }  # Alert triggered
            elif asin == watch2.asin:
                return None  # Failed check
            return None

        scheduler.keepa_client.query_product = AsyncMock(side_effect=mock_query_product)

        with patch(
            "src.scheduler.create_price_alert", new_callable=AsyncMock
        ) as mock_create_alert:
            mock_alert = MagicMock()
            mock_alert.id = uuid.uuid4()
            mock_create_alert.return_value = mock_alert

            with patch(
                "src.scheduler.get_pending_alerts_with_context", new_callable=AsyncMock
            ) as mock_pending:
                mock_pending.return_value = []
                result = await scheduler.run_price_check()

        # Verify summary structure
        assert result["total"] == 2
        assert result["successful"] == 1
        assert result["failed"] == 1
        assert result["price_changes"] == 1
        assert result["alerts_triggered"] == 1
        assert len(result["watches"]) == 2

        # Verify successful watch details
        success_watch = [w for w in result["watches"] if w["success"]][0]
        assert success_watch["current_price"] == 75.0
        assert success_watch["alert_triggered"] is True

        # Verify failed watch details
        failed_watch = [w for w in result["watches"] if not w["success"]][0]
        assert failed_watch["asin"] == watch2.asin


class TestRunImmediateCheck:
    """Test suite for run_immediate_check function"""

    @pytest.mark.asyncio
    @patch("src.scheduler.init_db")
    @patch("src.scheduler.get_active_watches")
    @patch("src.scheduler.update_watch_price")
    async def test_run_immediate_check_triggers_price_check(
        self, mock_update_watch_price, mock_get_active_watches, mock_init_db, mock_watch
    ):
        """Test 8: run_immediate_check triggers immediate price check for all watches"""
        mock_init_db.return_value = None
        mock_get_active_watches.return_value = [mock_watch]
        mock_update_watch_price.return_value = None

        with patch("src.scheduler.PriceMonitorScheduler") as MockScheduler:
            mock_scheduler_instance = MagicMock()
            mock_scheduler_instance.run_price_check = AsyncMock(
                return_value={
                    "total": 1,
                    "successful": 1,
                    "failed": 0,
                    "price_changes": 1,
                    "alerts_triggered": 0,
                    "watches": [],
                }
            )
            MockScheduler.return_value = mock_scheduler_instance

            with patch(
                "src.scheduler.get_pending_alerts_with_context", new_callable=AsyncMock
            ) as mock_pending:
                mock_pending.return_value = []
                result = await run_immediate_check()

        # Verify init_db was called
        mock_init_db.assert_called_once()

        # Verify scheduler was created with default interval
        MockScheduler.assert_called_once_with(check_interval=21600)

        # Verify run_price_check was called
        mock_scheduler_instance.run_price_check.assert_called_once()

        # Verify result
        assert result["total"] == 1
        assert result["successful"] == 1
