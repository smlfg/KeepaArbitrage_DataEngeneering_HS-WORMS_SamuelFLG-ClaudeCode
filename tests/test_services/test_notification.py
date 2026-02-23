import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime

from src.services.notification import NotificationService


@pytest.fixture
def notification_service():
    """Create a NotificationService instance for testing."""
    return NotificationService()


class TestSendEmail:
    """Tests for the send_email method."""

    async def test_send_email_success(self, notification_service):
        """Email send success — mock aiosmtplib.send, verify it's called with correct args."""
        with patch("src.services.notification.aiosmtplib.send") as mock_send:
            mock_send.return_value = None
            notification_service.settings.smtp_host = "smtp.gmail.com"
            notification_service.settings.smtp_port = 587
            notification_service.settings.smtp_user = "test@example.com"
            notification_service.settings.smtp_password = "password123"

            result = await notification_service.send_email(
                to="recipient@example.com",
                subject="Test Subject",
                html_body="<html><body>Test</body></html>",
                text_body="Test email body",
            )

            assert result["success"] is True
            assert "messageId" in result
            assert "timestamp" in result

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args.kwargs["hostname"] == "smtp.gmail.com"
            assert call_args.kwargs["port"] == 587
            assert call_args.kwargs["username"] == "test@example.com"
            assert call_args.kwargs["password"] == "password123"
            assert call_args.kwargs["use_tls"] is True

    async def test_send_email_smtp_not_configured(self, notification_service):
        """Email send when smtp not configured — should be no-op (no error)."""
        with patch("src.services.notification.aiosmtplib.send") as mock_send:
            notification_service.settings.smtp_host = None
            notification_service.settings.smtp_port = 587
            notification_service.settings.smtp_user = "test@example.com"
            notification_service.settings.smtp_password = "password123"

            result = await notification_service.send_email(
                to="recipient@example.com",
                subject="Test Subject",
                html_body="<html><body>Test</body></html>",
            )

            assert result["success"] is True
            assert "messageId" in result
            mock_send.assert_not_called()

    async def test_send_email_failure(self, notification_service):
        """Email send failure — exception from aiosmtplib is caught/logged, not re-raised."""
        with patch("src.services.notification.aiosmtplib.send") as mock_send:
            mock_send.side_effect = Exception("SMTP connection failed")
            notification_service.settings.smtp_host = "smtp.gmail.com"
            notification_service.settings.smtp_port = 587
            notification_service.settings.smtp_user = "test@example.com"
            notification_service.settings.smtp_password = "password123"

            result = await notification_service.send_email(
                to="recipient@example.com",
                subject="Test Subject",
                html_body="<html><body>Test</body></html>",
            )

            assert result["success"] is False
            assert "error" in result
            assert "SMTP connection failed" in result["error"]
            assert "timestamp" in result


class TestFormatPriceAlert:
    """Tests for the format_price_alert method."""

    def test_format_price_alert_email(self, notification_service):
        """format_price_alert for email — returns text string with price data."""
        result = notification_service.format_price_alert(
            product_name="Test Product",
            current_price=49.99,
            target_price=59.99,
            amazon_url="https://amazon.com/product/123",
            channel="email",
        )

        assert isinstance(result, dict)
        assert "subject" in result
        assert "body" in result
        assert "Test Product" in result["subject"]
        assert "49.99" in result["body"]
        assert "59.99" in result["body"]
        assert "10.00" in result["body"]  # savings
        assert "https://amazon.com/product/123" in result["body"]

    def test_format_price_alert_telegram(self, notification_service):
        """format_price_alert for telegram — returns Markdown/text string."""
        result = notification_service.format_price_alert(
            product_name="Test Product",
            current_price=49.99,
            target_price=59.99,
            amazon_url="https://amazon.com/product/123",
            channel="telegram",
        )

        assert isinstance(result, dict)
        assert "subject" in result
        assert "body" in result
        assert "Price Drop Alert!" in result["subject"]
        assert "*Price Drop Detected!*" in result["body"]
        assert "*Test Product*" in result["body"]
        assert "49.99" in result["body"]
        assert "[Buy on Amazon]" in result["body"]

    def test_format_price_alert_discord(self, notification_service):
        """format_price_alert for discord — returns embed dict or string."""
        result = notification_service.format_price_alert(
            product_name="Test Product",
            current_price=49.99,
            target_price=59.99,
            amazon_url="https://amazon.com/product/123",
            channel="discord",
        )

        assert isinstance(result, dict)
        assert "subject" in result
        assert "body" in result
        assert "Price Alert" in result["subject"]
        assert "**Price Drop!**" in result["body"]
        assert "Test Product" in result["body"]
        assert "49.99" in result["body"]
        assert "[Buy Now]" in result["body"]


class TestFormatDealReportHtml:
    """Tests for the format_deal_report_html method."""

    def test_format_deal_report_html_with_deals(self, notification_service):
        """format_deal_report_html — returns HTML string with deal list."""
        deals = [
            {
                "title": "Product A",
                "currentPrice": 29.99,
                "discountPercent": 25,
                "rating": 4.5,
                "reviewCount": 150,
                "amazonUrl": "https://amazon.com/product/a",
            },
            {
                "title": "Product B",
                "currentPrice": 49.99,
                "discountPercent": 30,
                "rating": 4.0,
                "reviewCount": 75,
                "amazonUrl": "https://amazon.com/product/b",
            },
        ]

        result = notification_service.format_deal_report_html(
            deals=deals,
            filter_name="Test Filter",
            filter_summary="Min 20% discount",
        )

        assert isinstance(result, str)
        assert "<html>" in result
        assert "Daily Deal Report" in result
        assert "Test Filter" in result
        assert "Min 20% discount" in result
        assert "Product A" in result
        assert "Product B" in result
        assert "29.99" in result
        assert "49.99" in result
        assert "25" in result
        assert "30" in result

    def test_format_deal_report_html_empty_deals(self, notification_service):
        """format_deal_report_html with empty deals — returns valid (possibly empty) string."""
        result = notification_service.format_deal_report_html(
            deals=[],
            filter_name="Test Filter",
            filter_summary="Min 50% discount",
        )

        assert isinstance(result, str)
        assert "<html>" in result
        assert "Daily Deal Report" in result
        assert "Test Filter" in result


class TestSendTelegram:
    """Tests for the send_telegram method."""

    async def test_send_telegram_success(self, notification_service):
        """send_telegram — when bot token + chat_id configured, calls httpx with correct URL."""
        with patch("src.services.notification.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "result": {"message_id": 12345},
            }
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            notification_service.settings.telegram_bot_token = "test_token_123"

            result = await notification_service.send_telegram(
                chat_id="123456789",
                text="Test message",
            )

            assert result["success"] is True
            assert result["messageId"] == "tg_12345"

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "test_token_123" in call_args.args[0]
            assert (
                call_args.args[0]
                == "https://api.telegram.org/bottest_token_123/sendMessage"
            )
            assert call_args.kwargs["json"]["chat_id"] == "123456789"
            assert call_args.kwargs["json"]["text"] == "Test message"
            assert call_args.kwargs["json"]["parse_mode"] == "Markdown"

    async def test_send_telegram_not_configured(self, notification_service):
        """send_telegram — when not configured, is a no-op."""
        notification_service.settings.telegram_bot_token = None

        result = await notification_service.send_telegram(
            chat_id="123456789",
            text="Test message",
        )

        assert result["success"] is False
        assert "error" in result
        assert "Telegram not configured" in result["error"]


class TestSendDiscord:
    """Tests for the send_discord method."""

    async def test_send_discord_success(self, notification_service):
        """send_discord — when webhook configured, posts to webhook URL."""
        with patch("src.services.notification.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 204
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            webhook_url = "https://discord.com/api/webhooks/123/abc"

            result = await notification_service.send_discord(
                webhook_url=webhook_url,
                content="Test discord message",
            )

            assert result["success"] is True
            assert "messageId" in result
            assert result["messageId"].startswith("dc_")

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args.args[0] == webhook_url
            assert call_args.kwargs["json"] == {"content": "Test discord message"}

    async def test_send_discord_not_configured(self, notification_service):
        """send_discord — when not configured, is a no-op."""
        result = await notification_service.send_discord(
            webhook_url="",
            content="Test discord message",
        )

        assert result["success"] is False
        assert "error" in result
        assert "Discord not configured" in result["error"]
