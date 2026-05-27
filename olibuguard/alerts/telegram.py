"""Telegram alert sink: sends messages via the Bot API using stdlib urllib.

No extra dependencies required. Messages are plain text (no HTML/Markdown) to
avoid escaping issues with symbols such as BTC/USDT.

Credentials (never hardcoded):
  TELEGRAM_BOT_TOKEN  — bot token from @BotFather
  TELEGRAM_CHAT_ID    — your personal or group chat ID

The sink is synchronous and blocks for up to *timeout* seconds; all calls in
the adapter are wrapped in run_safe so a slow or unreachable Telegram API
never blocks trading.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TelegramAlertSink:
    """Send plain-text alerts to a Telegram chat via the Bot API.

    Args:
        token:   Telegram bot token (from @BotFather).
        chat_id: Target chat ID (personal, group, or channel).
        timeout: HTTP request timeout in seconds (default 5).
    """

    def __init__(self, token: str, chat_id: str, timeout: float = 5.0) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def send(self, message: str) -> None:
        """Send *message* to the configured chat. Raises on network/API error."""
        payload = json.dumps({"chat_id": self._chat_id, "text": message}).encode()
        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout):
            pass
