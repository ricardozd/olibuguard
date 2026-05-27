"""Alert layer: push notifications for critical bot events.

Default sink is NullAlertSink (no-op). A failing sink must never block
trading; callers always wrap sends in run_safe (fail-safe).

Supported backends (opt-in via environment variables):
  - TelegramAlertSink: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.
"""
