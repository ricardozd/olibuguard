"""Audit sink port and the default no-op implementation.

The bot must work without persistence (NullAuditSink is the default). A failing
sink must never block trading; callers wrap calls and continue (fail-safe).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from olibuguard.audit.records import DecisionAudit, EquityPoint


@runtime_checkable
class AuditSink(Protocol):
    def record_decision(self, audit: DecisionAudit) -> None: ...

    def record_equity(self, point: EquityPoint) -> None: ...


class NullAuditSink:
    """Default sink: records nothing."""

    def record_decision(self, audit: DecisionAudit) -> None:
        return None

    def record_equity(self, point: EquityPoint) -> None:
        return None
