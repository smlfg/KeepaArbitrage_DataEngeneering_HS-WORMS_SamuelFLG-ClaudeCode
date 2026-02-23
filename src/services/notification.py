from typing import Dict, Any, Optional
from datetime import datetime
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from jinja2 import Template
import httpx
from src.config import get_settings


class NotificationService:
    def __init__(self):
        self.settings = get_settings()

    async def send_email(
        self, to: str, subject: str, html_body: str, text_body: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"alerts@keeper.app"
            msg["To"] = to

            if text_body:
                msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            if self.settings.smtp_host:
                await aiosmtplib.send(
                    msg,
                    hostname=self.settings.smtp_host,
                    port=self.settings.smtp_port,
                    username=self.settings.smtp_user,
                    password=self.settings.smtp_password,
                    use_tls=True,
                )

            return {
                "success": True,
                "messageId": f"email_{datetime.utcnow().timestamp()}",
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

    def format_price_alert(
        self,
        product_name: str,
        current_price: float,
        target_price: float,
        amazon_url: str,
        channel: str = "email",
    ) -> Dict[str, str]:
        savings = target_price - current_price
        if channel == "telegram":
            return {
                "subject": "🚨 Price Drop Alert!",
                "body": f"🎉 *Price Drop Detected!*\n\n*{product_name}*\n💰 {current_price:.2f}€ < {target_price:.2f}€\n📉 Savings: {savings:.2f}€\n\n[Buy on Amazon]({amazon_url})",
            }
        elif channel == "discord":
            return {
                "subject": "💰 Price Alert",
                "body": f"**Price Drop!**\n{product_name}\n{current_price:.2f}€ (Target: {target_price:.2f}€)\n[Buy Now]({amazon_url})",
            }
        else:
            return {
                "subject": f"🎉 Price Drop Alert: {product_name}",
                "body": f"""
Hi,

Great news! The product you're watching has dropped in price!

📦 {product_name}
💰 Current Price: {current_price:.2f}€
🎯 Your Target: {target_price:.2f}€
📉 Savings: {savings:.2f}€

[Buy Now on Amazon]({amazon_url})

Happy shopping!
Keeper Team
                """,
            }

    def format_deal_report_html(
        self, deals: list, filter_name: str, filter_summary: str
    ) -> str:
        deals_html = ""
        for i, deal in enumerate(deals, 1):
            url = deal.get("url", deal.get("amazonUrl", "#"))
            rating = deal.get("rating", "N/A")
            reviews = deal.get("reviews", deal.get("reviewCount", 0))
            current_price = deal.get("current_price", deal.get("currentPrice", "N/A"))
            discount = deal.get(
                "discount_percent", deal.get("discountPercent", 0)
            )
            deals_html += f"""
            <tr>
                <td>{i}</td>
                <td>
                    <a href="{url}">{deal.get("title", "Unknown")}</a><br>
                    ⭐ {rating}/5 ({reviews} reviews)
                </td>
                <td>{current_price}€</td>
                <td style="color:red; font-weight:bold">-{discount}%</td>
            </tr>
            """

        return f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
                th {{ background-color: #f0f0f0; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; }}
                .footer {{ margin-top: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🔥 Daily Deal Report</h1>
                <p>Filter: {filter_summary}</p>
            </div>
            <table>
                <tr>
                    <th>#</th>
                    <th>Product</th>
                    <th>Price</th>
                    <th>Discount</th>
                </tr>
                {deals_html}
            </table>
            <div class="footer">
                <p>You received this because you subscribed to {filter_name}.</p>
                <p><a href="#">Unsubscribe</a> | <a href="#">Manage Filters</a></p>
            </div>
        </body>
        </html>
        """

    async def send_telegram(self, chat_id: str, text: str) -> Dict[str, Any]:
        """Send Telegram message if configured; otherwise fail gracefully."""
        token = self.settings.telegram_bot_token
        if not token or not chat_id:
            return {"success": False, "error": "Telegram not configured"}

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200 and resp.json().get("ok"):
                    msg_id = resp.json().get("result", {}).get("message_id")
                    return {"success": True, "messageId": f"tg_{msg_id}"}
                return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_discord(self, webhook_url: str, content: str) -> Dict[str, Any]:
        """Send Discord webhook if configured; otherwise fail gracefully."""
        if not webhook_url:
            return {"success": False, "error": "Discord not configured"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json={"content": content})
                if 200 <= resp.status_code < 300:
                    return {
                        "success": True,
                        "messageId": f"dc_{datetime.utcnow().timestamp()}",
                    }
                return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}


notification_service = NotificationService()
