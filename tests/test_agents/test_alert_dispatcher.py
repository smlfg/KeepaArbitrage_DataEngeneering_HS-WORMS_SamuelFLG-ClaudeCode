import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from src.agents.alert_dispatcher import AlertDispatcherAgent, alert_dispatcher


@pytest.fixture
def agent():
    return AlertDispatcherAgent()


@pytest.fixture
def sample_alert():
    return {
        "id": "alert123",
        "user_id": "user456",
        "asin": "B08N5WRWNW",
        "product_name": "Test Product",
        "current_price": 89.99,
        "target_price": 100.00,
        "amazon_url": "https://amazon.com/dp/B08N5WRWNW",
        "email": "test@example.com",
        "channels": ["email"],
    }


@pytest.fixture
def sample_recent_alerts():
    return []


class TestAlertDispatcherInit:
    def test_init_creates_sent_alerts_dict(self, agent):
        assert hasattr(agent, "sent_alerts")
        assert isinstance(agent.sent_alerts, dict)
        assert len(agent.sent_alerts) == 0

    def test_init_default_constants(self, agent):
        assert agent.MAX_ALERTS_PER_HOUR == 10
        assert agent.DUPLICATE_WINDOW == timedelta(hours=1)
        assert agent.MAX_RETRIES == 3
        assert agent.RETRY_DELAYS == [0, 30, 120]


class TestValidateAlertInput:
    def test_validate_alert_valid_passes(self, agent, sample_alert):
        valid, message = agent.validate_alert_input(sample_alert)
        assert valid is True
        assert message == "Valid"

    def test_validate_alert_missing_user_id_fails(self, agent, sample_alert):
        alert = sample_alert.copy()
        del alert["user_id"]
        valid, message = agent.validate_alert_input(alert)
        assert valid is False
        assert "user_id" in message.lower()

    def test_validate_alert_missing_product_name_fails(self, agent, sample_alert):
        alert = sample_alert.copy()
        del alert["product_name"]
        valid, message = agent.validate_alert_input(alert)
        assert valid is False
        assert "product_name" in message.lower()

    def test_validate_alert_missing_channels_fails(self, agent, sample_alert):
        alert = sample_alert.copy()
        alert["channels"] = []
        valid, message = agent.validate_alert_input(alert)
        assert valid is False

    def test_validate_alert_missing_price_fails(self, agent, sample_alert):
        alert = sample_alert.copy()
        del alert["current_price"]
        valid, message = agent.validate_alert_input(alert)
        assert valid is False


class TestIsDuplicateAlert:
    def test_is_duplicate_alert_returns_true_when_recent(self, agent):
        user_id = "user123"
        asin = "B08N5WRWNW"
        channel = "email"

        agent.mark_alert_sent(user_id, asin, channel)

        result = agent.is_duplicate_alert(user_id, asin, channel)
        assert result is True

    def test_is_duplicate_alert_returns_false_when_old(self, agent):
        user_id = "user123"
        asin = "B08N5WRWNW"
        channel = "email"

        old_time = datetime.utcnow() - timedelta(hours=2)
        agent.sent_alerts[f"{user_id}_{asin}_{channel}"] = old_time

        result = agent.is_duplicate_alert(user_id, asin, channel)
        assert result is False

    def test_is_duplicate_alert_returns_false_when_new(self, agent):
        result = agent.is_duplicate_alert("new_user", "B123", "email")
        assert result is False


class TestMarkAlertSent:
    def test_mark_alert_sent_records_timestamp(self, agent):
        user_id = "user123"
        asin = "B08N5WRWNW"
        channel = "email"

        agent.mark_alert_sent(user_id, asin, channel)

        key = f"{user_id}_{asin}_{channel}"
        assert key in agent.sent_alerts
        assert isinstance(agent.sent_alerts[key], datetime)


class TestFormatAlert:
    def test_format_alert_returns_non_empty_string(self, agent, sample_alert):
        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={
                    "subject": "Price Drop Alert",
                    "body": "The price has dropped!",
                }
            )

            result = agent.format_alert(sample_alert, "email")

            assert "subject" in result
            assert "body" in result
            assert result["body"] != ""

    def test_format_alert_different_channels(self, agent, sample_alert):
        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Message"}
            )

            result_email = agent.format_alert(sample_alert, "email")
            result_telegram = agent.format_alert(sample_alert, "telegram")

            assert "body" in result_email
            assert "body" in result_telegram


class TestCheckRateLimit:
    def test_check_rate_limit_not_exceeded(self, agent):
        recent_alerts = []
        result = agent.check_rate_limit("user123", recent_alerts)

        assert result["exceeded"] is False
        assert result["remaining"] == 10

    def test_check_rate_limit_exceeded(self, agent):
        recent_alerts = [
            {
                "user_id": "user123",
                "sent_at": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
            }
            for _ in range(12)
        ]
        result = agent.check_rate_limit("user123", recent_alerts)

        assert result["exceeded"] is True
        assert "Rate limit exceeded" in result["message"]


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_send_alert_email_sends_when_configured(self, agent, sample_alert):
        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Body"}
            )
            mock_service.send_email = AsyncMock(
                return_value={"success": True, "messageId": "123"}
            )

            result = await agent.send_alert(sample_alert, "email")

            assert result["success"] is True
            mock_service.send_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_telegram_sends_when_configured(self, agent, sample_alert):
        alert = sample_alert.copy()
        alert["telegram_chat_id"] = "123456"

        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"body": "Message"}
            )
            mock_service.send_telegram = AsyncMock(return_value={"success": True})

            result = await agent.send_alert(alert, "telegram")

            assert result["success"] is True
            mock_service.send_telegram.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_discord_sends_when_configured(self, agent, sample_alert):
        alert = sample_alert.copy()
        alert["discord_webhook"] = "https://discord.com/webhook"

        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"body": "Message"}
            )
            mock_service.send_discord = AsyncMock(return_value={"success": True})

            result = await agent.send_alert(alert, "discord")

            assert result["success"] is True
            mock_service.send_discord.assert_called_once()


class TestDispatchAlert:
    @pytest.mark.asyncio
    async def test_dispatch_alert_invalid_input_fails(self, agent):
        result = await agent.dispatch_alert({})
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_dispatch_alert_skips_duplicate(self, agent, sample_alert):
        agent.mark_alert_sent(sample_alert["user_id"], sample_alert["asin"], "email")

        result = await agent.dispatch_alert(sample_alert)

        assert "channel_results" in result
        assert result["channel_results"]["email"]["skipped"] is True

    @pytest.mark.asyncio
    async def test_dispatch_alert_sends_to_all_channels(self, agent, sample_alert):
        alert = sample_alert.copy()
        alert["channels"] = ["email", "telegram", "discord"]
        alert["telegram_chat_id"] = "123"
        alert["discord_webhook"] = "https://webhook"

        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Body"}
            )
            mock_service.send_email = AsyncMock(return_value={"success": True})
            mock_service.send_telegram = AsyncMock(return_value={"success": True})
            mock_service.send_discord = AsyncMock(return_value={"success": True})

            result = await agent.dispatch_alert(alert)

            assert result["success"] is True
            assert "email" in result["channel_results"]
            assert "telegram" in result["channel_results"]
            assert "discord" in result["channel_results"]

    @pytest.mark.asyncio
    async def test_dispatch_alert_handles_failure_with_retry(self, agent, sample_alert):
        with patch("src.agents.alert_dispatcher.notification_service") as mock_service, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Body"}
            )
            mock_service.send_email = AsyncMock(
                return_value={"success": False, "error": "Failed"}
            )

            result = await agent.dispatch_alert(sample_alert, ["email"])

            assert mock_service.send_email.call_count >= 1
            assert mock_sleep.call_count >= 1


class TestDispatchBatch:
    @pytest.mark.asyncio
    async def test_dispatch_batch_processes_multiple_alerts(self, agent, sample_alert):
        alerts = [sample_alert.copy() for _ in range(3)]

        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Body"}
            )
            mock_service.send_email = AsyncMock(return_value={"success": True})

            result = await agent.dispatch_batch(alerts, "user456")

            assert result["total"] == 3
            assert result["sent"] + result["failed"] + result["skipped"] == 3

    @pytest.mark.asyncio
    async def test_dispatch_batch_handles_empty_list(self, agent):
        result = await agent.dispatch_batch([], "user123")

        assert result["total"] == 0
        assert result["sent"] == 0
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_dispatch_batch_handles_mixed_results(self, agent):
        alerts = [
            {
                "id": "1",
                "user_id": "u1",
                "product_name": "P1",
                "current_price": 10,
                "target_price": 20,
                "channels": ["email"],
            },
            {
                "id": "2",
                "user_id": "u1",
                "product_name": "P2",
                "current_price": 10,
                "target_price": 20,
                "channels": [],
            },
        ]

        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Body"}
            )
            mock_service.send_email = AsyncMock(return_value={"success": True})

            result = await agent.dispatch_batch(alerts, "u1")

            assert result["total"] == 2


class TestDispatchAlertDeduplication:
    @pytest.mark.asyncio
    async def test_dispatch_alert_not_sent_twice_in_short_window(
        self, agent, sample_alert
    ):
        with patch("src.agents.alert_dispatcher.notification_service") as mock_service:
            mock_service.format_price_alert = MagicMock(
                return_value={"subject": "Alert", "body": "Body"}
            )
            mock_service.send_email = AsyncMock(return_value={"success": True})

            result1 = await agent.dispatch_alert(sample_alert)
            result2 = await agent.dispatch_alert(sample_alert)

            assert result1["channel_results"]["email"].get("skipped") is None
            assert result2["channel_results"]["email"]["skipped"] is True


class TestAlertDispatcherGlobal:
    def test_alert_dispatcher_instance_exists(self):
        assert alert_dispatcher is not None
        assert isinstance(alert_dispatcher, AlertDispatcherAgent)
