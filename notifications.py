"""
notifications.py
----------------
Multi-channel notification system for trading alerts.

Supports:
- Telegram
- Email
- Webhook (Discord, Slack, custom)
- In-app notifications
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
import datetime as dt
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from enum import Enum


class NotificationLevel(Enum):
    """Notification priority levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationChannel(Enum):
    """Notification channels."""
    TELEGRAM = "telegram"
    EMAIL = "email"
    WEBHOOK = "webhook"
    IN_APP = "in_app"


@dataclass
class Notification:
    """A single notification event."""
    id: str
    channel: NotificationChannel
    level: NotificationLevel
    title: str
    message: str
    timestamp: str
    is_sent: bool = False
    delivery_status: str = ""


class NotificationManager:
    """
    Central notification system.

    Manages multiple channels and ensures traders are alerted to:
    - Order execution events
    - Risk limit warnings
    - System errors
    - P&L milestones
    """

    def __init__(self, config_file: str = "notifications_config.json"):
        self.config_file = config_file
        self.config = self._load_config()
        self.notification_history: List[Notification] = []
        self.enabled_channels = set()

        self._validate_channels()

    # =====================================================================
    # CHANNEL CONFIGURATION
    # =====================================================================

    def _load_config(self) -> Dict:
        """Load notification config from file."""
        default_config = {
            "telegram": {
                "enabled": False,
                "bot_token": "",
                "chat_id": "",
            },
            "email": {
                "enabled": False,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "sender_email": "",
                "sender_password": "",
                "recipient_email": "",
            },
            "webhook": {
                "enabled": False,
                "discord_webhook": "",
                "slack_webhook": "",
                "custom_webhooks": [],
            },
            "in_app": {
                "enabled": True,
                "max_history": 100,
            },
        }

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    loaded = json.load(f)
                    # Merge with defaults
                    for key in default_config:
                        if key in loaded:
                            default_config[key].update(loaded[key])
                    return default_config
            except Exception as e:
                print(f"Error loading notification config: {e}")

        return default_config

    def save_config(self) -> None:
        """Save notification config to file."""
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving notification config: {e}")

    def _validate_channels(self) -> None:
        """Validate and enable configured channels."""
        if self.config["telegram"]["enabled"] and self.config["telegram"].get("bot_token"):
            self.enabled_channels.add(NotificationChannel.TELEGRAM)

        if self.config["email"]["enabled"] and self.config["email"].get("sender_email"):
            self.enabled_channels.add(NotificationChannel.EMAIL)

        if self.config["webhook"]["enabled"]:
            if (self.config["webhook"].get("discord_webhook") or
                self.config["webhook"].get("slack_webhook") or
                self.config["webhook"].get("custom_webhooks")):
                self.enabled_channels.add(NotificationChannel.WEBHOOK)

        if self.config["in_app"]["enabled"]:
            self.enabled_channels.add(NotificationChannel.IN_APP)

    def enable_telegram(self, bot_token: str, chat_id: str) -> None:
        """Enable Telegram notifications."""
        self.config["telegram"]["enabled"] = True
        self.config["telegram"]["bot_token"] = bot_token
        self.config["telegram"]["chat_id"] = chat_id
        self.enabled_channels.add(NotificationChannel.TELEGRAM)
        self.save_config()

    def enable_email(
        self,
        smtp_server: str,
        smtp_port: int,
        sender_email: str,
        sender_password: str,
        recipient_email: str,
    ) -> None:
        """Enable email notifications."""
        self.config["email"]["enabled"] = True
        self.config["email"]["smtp_server"] = smtp_server
        self.config["email"]["smtp_port"] = smtp_port
        self.config["email"]["sender_email"] = sender_email
        self.config["email"]["sender_password"] = sender_password
        self.config["email"]["recipient_email"] = recipient_email
        self.enabled_channels.add(NotificationChannel.EMAIL)
        self.save_config()

    def enable_webhook(
        self,
        discord_webhook: str = "",
        slack_webhook: str = "",
        custom_webhooks: List[str] = None,
    ) -> None:
        """Enable webhook notifications."""
        self.config["webhook"]["enabled"] = True
        if discord_webhook:
            self.config["webhook"]["discord_webhook"] = discord_webhook
        if slack_webhook:
            self.config["webhook"]["slack_webhook"] = slack_webhook
        if custom_webhooks:
            self.config["webhook"]["custom_webhooks"] = custom_webhooks
        self.enabled_channels.add(NotificationChannel.WEBHOOK)
        self.save_config()

    # =====================================================================
    # TRADING ALERTS
    # =====================================================================

    def notify_order_placed(
        self,
        symbol: str,
        qty: int,
        price: float,
        order_type: str,
        order_id: str,
    ) -> None:
        """Notify when order is placed."""
        title = f"🚀 Order Placed: {symbol}"
        message = f"""
Symbol: {symbol}
Quantity: {qty}
Price: ₹{price:.2f}
Type: {order_type}
Order ID: {order_id}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.INFO,
        )

    def notify_order_filled(
        self,
        symbol: str,
        qty: int,
        fill_price: float,
        order_id: str,
    ) -> None:
        """Notify when order is filled."""
        title = f"✅ Order Filled: {symbol}"
        message = f"""
Symbol: {symbol}
Quantity: {qty}
Fill Price: ₹{fill_price:.2f}
Order ID: {order_id}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.INFO,
        )

    def notify_order_rejected(
        self,
        symbol: str,
        order_id: str,
        reason: str,
    ) -> None:
        """Notify when order is rejected."""
        title = f"❌ Order Rejected: {symbol}"
        message = f"""
Symbol: {symbol}
Order ID: {order_id}
Reason: {reason}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.ERROR,
        )

    def notify_stop_loss_hit(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        loss_amount: float,
    ) -> None:
        """Notify when stop-loss is hit."""
        title = f"🛑 Stop Loss Hit: {symbol}"
        message = f"""
Symbol: {symbol}
Entry Price: ₹{entry_price:.2f}
Stop Price: ₹{stop_price:.2f}
Loss: ₹{loss_amount:.2f}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.WARNING,
        )

    def notify_target_hit(
        self,
        symbol: str,
        entry_price: float,
        target_price: float,
        profit_amount: float,
    ) -> None:
        """Notify when target is hit."""
        title = f"🎯 Target Hit: {symbol}"
        message = f"""
Symbol: {symbol}
Entry Price: ₹{entry_price:.2f}
Target Price: ₹{target_price:.2f}
Profit: ₹{profit_amount:.2f}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.INFO,
        )

    def notify_daily_loss_warning(self, loss_pct: float, limit_pct: float) -> None:
        """Notify when approaching daily loss limit."""
        title = f"⚠️ Daily Loss Warning"
        message = f"""
Current Loss: {loss_pct:.2f}%
Limit: {limit_pct:.2f}%
Remaining Budget: {limit_pct - loss_pct:.2f}%
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.WARNING,
        )

    def notify_daily_loss_hit(self, loss_pct: float) -> None:
        """Notify when daily loss limit is hit."""
        title = f"🔴 Daily Loss Limit Hit"
        message = f"""
Loss: {loss_pct:.2f}%
Trading paused for today.
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.CRITICAL,
        )

    def notify_drawdown_warning(self, drawdown_pct: float, limit_pct: float) -> None:
        """Notify when drawdown approaches limit."""
        title = f"⚠️ Drawdown Warning"
        message = f"""
Current Drawdown: {drawdown_pct:.2f}%
Limit: {limit_pct:.2f}%
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.WARNING,
        )

    def notify_api_failure(self, broker: str, error: str) -> None:
        """Notify when broker API fails."""
        title = f"❌ API Failure: {broker}"
        message = f"""
Broker: {broker}
Error: {error}
Time: {dt.datetime.now().strftime('%H:%M:%S')}

Action: Check your internet connection and broker status.
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.CRITICAL,
        )

    def notify_data_feed_failure(self, error: str) -> None:
        """Notify when data feed fails."""
        title = f"❌ Data Feed Failure"
        message = f"""
Error: {error}
Time: {dt.datetime.now().strftime('%H:%M:%S')}

Action: Scanning paused until data feed recovers.
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.CRITICAL,
        )

    def notify_bot_status_change(self, new_status: str, reason: str = "") -> None:
        """Notify when bot status changes."""
        status_icons = {
            "RUNNING": "🟢",
            "PAUSED": "🟡",
            "STOPPED": "🔴",
        }
        icon = status_icons.get(new_status, "⚪")

        title = f"{icon} Bot Status: {new_status}"
        message = f"""
Status: {new_status}
Reason: {reason or "Manual control"}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        level = NotificationLevel.INFO if new_status == "RUNNING" else NotificationLevel.WARNING

        self.send_notification(
            title=title,
            message=message,
            level=level,
        )

    def notify_signal_generated(
        self,
        symbol: str,
        signal_type: str,
        strength: float,
        filters_passed: int,
        filters_total: int,
    ) -> None:
        """Notify when a trading signal is generated."""
        title = f"📡 Signal: {signal_type} - {symbol}"
        message = f"""
Symbol: {symbol}
Signal: {signal_type}
Strength: {strength * 100:.0f}%
Filters Passed: {filters_passed}/{filters_total}
Time: {dt.datetime.now().strftime('%H:%M:%S')}
        """.strip()

        self.send_notification(
            title=title,
            message=message,
            level=NotificationLevel.INFO,
        )

    # =====================================================================
    # CORE NOTIFICATION SENDING
    # =====================================================================

    def send_notification(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        channels: Optional[List[NotificationChannel]] = None,
    ) -> None:
        """Send notification to configured channels."""
        if channels is None:
            channels = list(self.enabled_channels)

        notification_id = f"notif_{dt.datetime.now().timestamp()}"

        for channel in channels:
            if channel not in self.enabled_channels:
                continue

            try:
                if channel == NotificationChannel.TELEGRAM:
                    self._send_telegram(title, message, level)

                elif channel == NotificationChannel.EMAIL:
                    self._send_email(title, message, level)

                elif channel == NotificationChannel.WEBHOOK:
                    self._send_webhook(title, message, level)

                elif channel == NotificationChannel.IN_APP:
                    self._send_in_app(notification_id, title, message, level)

            except Exception as e:
                print(f"Error sending {channel.value} notification: {e}")

    def _send_telegram(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
    ) -> None:
        """Send notification via Telegram."""
        bot_token = self.config["telegram"]["bot_token"]
        chat_id = self.config["telegram"]["chat_id"]

        if not bot_token or not chat_id:
            return

        level_emoji = {
            NotificationLevel.INFO: "ℹ️",
            NotificationLevel.WARNING: "⚠️",
            NotificationLevel.ERROR: "❌",
            NotificationLevel.CRITICAL: "🚨",
        }

        text = f"{level_emoji.get(level, '')} *{title}*\n\n{message}"

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Telegram error: {e}")

    def _send_email(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
    ) -> None:
        """Send notification via email."""
        smtp_server = self.config["email"]["smtp_server"]
        smtp_port = self.config["email"]["smtp_port"]
        sender_email = self.config["email"]["sender_email"]
        sender_password = self.config["email"]["sender_password"]
        recipient_email = self.config["email"]["recipient_email"]

        if not all([sender_email, sender_password, recipient_email]):
            return

        try:
            msg = MIMEMultipart()
            msg["From"] = sender_email
            msg["To"] = recipient_email
            msg["Subject"] = title

            body = f"{title}\n\n{message}\n\nTime: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)

        except Exception as e:
            print(f"Email error: {e}")

    def _send_webhook(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
    ) -> None:
        """Send notification via webhooks (Discord, Slack, custom)."""
        webhooks = self.config["webhook"]

        # Discord
        if webhooks.get("discord_webhook"):
            try:
                color_map = {
                    NotificationLevel.INFO: 0x0099FF,
                    NotificationLevel.WARNING: 0xFFCC00,
                    NotificationLevel.ERROR: 0xFF3333,
                    NotificationLevel.CRITICAL: 0xFF0000,
                }
                color = color_map.get(level, 0x000000)

                payload = {
                    "embeds": [{
                        "title": title,
                        "description": message,
                        "color": color,
                        "timestamp": dt.datetime.now().isoformat(),
                    }]
                }

                requests.post(webhooks["discord_webhook"], json=payload, timeout=5)
            except Exception as e:
                print(f"Discord webhook error: {e}")

        # Slack
        if webhooks.get("slack_webhook"):
            try:
                color_map = {
                    NotificationLevel.INFO: "#0099FF",
                    NotificationLevel.WARNING: "#FFCC00",
                    NotificationLevel.ERROR: "#FF3333",
                    NotificationLevel.CRITICAL: "#FF0000",
                }
                color = color_map.get(level, "#000000")

                payload = {
                    "attachments": [{
                        "title": title,
                        "text": message,
                        "color": color,
                        "ts": int(dt.datetime.now().timestamp()),
                    }]
                }

                requests.post(webhooks["slack_webhook"], json=payload, timeout=5)
            except Exception as e:
                print(f"Slack webhook error: {e}")

    def _send_in_app(
        self,
        notification_id: str,
        title: str,
        message: str,
        level: NotificationLevel,
    ) -> None:
        """Add notification to in-app history."""
        notification = Notification(
            id=notification_id,
            channel=NotificationChannel.IN_APP,
            level=level,
            title=title,
            message=message,
            timestamp=dt.datetime.now().isoformat(),
            is_sent=True,
        )

        self.notification_history.append(notification)

        # Keep only recent notifications
        max_history = self.config["in_app"]["max_history"]
        if len(self.notification_history) > max_history:
            self.notification_history = self.notification_history[-max_history:]

    # =====================================================================
    # QUERYING
    # =====================================================================

    def get_recent_notifications(self, limit: int = 20) -> List[Notification]:
        """Get recent notifications."""
        return self.notification_history[-limit:]

    def get_notifications_by_level(self, level: NotificationLevel) -> List[Notification]:
        """Get notifications by level."""
        return [n for n in self.notification_history if n.level == level]

    def clear_history(self) -> None:
        """Clear notification history."""
        self.notification_history.clear()
