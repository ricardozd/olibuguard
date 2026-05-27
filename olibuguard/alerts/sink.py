"""Alert sink port and the default no-op implementation.

Pattern mirrors AuditSink: Protocol port + NullAlertSink default so the bot
works without any notification infrastructure configured.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AlertSink(Protocol):
    """Port for outbound alert notifications."""

    def send(self, message: str) -> None: ...


class NullAlertSink:
    """Default sink: silently discards all alerts."""

    def send(self, message: str) -> None:
        return None
