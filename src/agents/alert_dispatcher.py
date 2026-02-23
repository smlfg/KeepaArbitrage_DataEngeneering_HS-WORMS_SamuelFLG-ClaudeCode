from src.services.notification import notification_service
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio
import re


class AlertDispatcherAgent:
    MAX_ALERTS_PER_HOUR = 10
    DUPLICATE_WINDOW = timedelta(hours=1)
    MAX_RETRIES = 3
    RETRY_DELAYS = [0, 30, 120]

    def __init__(self):
        self.sent_alerts = {}

    def validate_alert_input(self, alert: Dict[str, Any]) -> tuple:
        if not alert.get("user_id"):
            return False, "Missing user_id"

        if not alert.get("product_name"):
            return False, "Missing product_name"

        channels = alert.get("channels", ["email"])
        if not channels:
            return False, "No notification channels enabled"

        if not alert.get("current_price") or not alert.get("target_price"):
            return False, "Missing price information"

        return True, "Valid"

    def is_duplicate_alert(self, user_id: str, asin: str, channel: str) -> bool:
        key = f"{user_id}_{asin}_{channel}"
        last_sent = self.sent_alerts.get(key)

        if last_sent and (datetime.utcnow() - last_sent) < self.DUPLICATE_WINDOW:
            return True

        return False

    def mark_alert_sent(self, user_id: str, asin: str, channel: str):
        key = f"{user_id}_{asin}_{channel}"
        self.sent_alerts[key] = datetime.utcnow()

    def format_alert(
        self, alert: Dict[str, Any], channel: str = "email"
    ) -> Dict[str, str]:
        return notification_service.format_price_alert(
            product_name=alert.get("product_name", ""),
            current_price=alert.get("current_price", 0),
            target_price=alert.get("target_price", 0),
            amazon_url=alert.get("amazon_url", ""),
            channel=channel,
        )

    def check_rate_limit(
        self, user_id: str, recent_alerts: List[Dict]
    ) -> Dict[str, Any]:
        hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_count = len(
            [
                a
                for a in recent_alerts
                if a.get("user_id") == user_id
                and a.get("sent_at")
                and a["sent_at"] > hour_ago.isoformat()
            ]
        )

        if recent_count >= self.MAX_ALERTS_PER_HOUR:
            return {
                "exceeded": True,
                "message": f"Rate limit exceeded: {recent_count} alerts in last hour",
                "suggestion": "Queue for later or send summary",
            }

        return {"exceeded": False, "remaining": self.MAX_ALERTS_PER_HOUR - recent_count}

    async def send_alert(
        self, alert: Dict[str, Any], channel: str = "email"
    ) -> Dict[str, Any]:
        formatted = self.format_alert(alert, channel)

        if channel == "email":
            to_addr = alert.get("email") or "user@example.com"
            return await notification_service.send_email(
                to=to_addr,
                subject=formatted["subject"],
                text_body=formatted["body"],
                html_body=formatted["body"],
            )
        if channel == "telegram":
            return await notification_service.send_telegram(
                chat_id=alert.get("telegram_chat_id", ""), text=formatted["body"]
            )
        if channel == "discord":
            return await notification_service.send_discord(
                webhook_url=alert.get("discord_webhook", ""), content=formatted["body"]
            )

        return {"success": False, "error": f"Channel {channel} not implemented"}

    async def dispatch_alert(
        self, alert: Dict[str, Any], channels: List[str] = None
    ) -> Dict[str, Any]:
        valid, message = self.validate_alert_input(alert)
        if not valid:
            return {"success": False, "error": message}

        if channels is None:
            channels = alert.get("channels", ["email"])

        user_id = alert.get("user_id", "unknown")
        asin = alert.get("asin", "unknown")

        results = {}
        for channel in channels:
            if self.is_duplicate_alert(user_id, asin, channel):
                results[channel] = {
                    "success": True,
                    "skipped": True,
                    "reason": "Duplicate alert",
                }
                continue

            result = await self.send_alert(alert, channel)

            if result.get("success"):
                self.mark_alert_sent(user_id, asin, channel)
                results[channel] = {
                    "success": True,
                    "messageId": result.get("messageId"),
                }
            else:
                for retry in range(self.MAX_RETRIES):
                    await asyncio.sleep(self.RETRY_DELAYS[retry])
                    result = await self.send_alert(alert, channel)
                    if result.get("success"):
                        break

                results[channel] = result

        overall_success = any(r.get("success") for r in results.values())

        return {
            "success": overall_success,
            "channel_results": results,
            "alert_id": alert.get("id", "unknown"),
        }

    async def dispatch_batch(
        self, alerts: List[Dict[str, Any]], user_id: str
    ) -> Dict[str, Any]:
        sent = 0
        failed = 0
        skipped = 0

        for alert in alerts:
            result = await self.dispatch_alert(alert)

            if result.get("success"):
                if result.get("channel_results", {}).get("email", {}).get("skipped"):
                    skipped += 1
                else:
                    sent += 1
            else:
                failed += 1

        return {
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "total": len(alerts),
        }


alert_dispatcher = AlertDispatcherAgent()
