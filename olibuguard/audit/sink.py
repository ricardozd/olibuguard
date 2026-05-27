"""Audit sink port and the default no-op implementation.

The bot must work without persistence (NullAuditSink is the default). A failing
sink must never block trading; callers wrap calls and continue (fail-safe).

Two protocols:
- AuditSink  — write side (record decisions and equity snapshots).
- AuditReader — read side (reconciliation queries). Implemented only by
  SQLiteAuditSink; use ``isinstance(sink, AuditReader)`` before calling.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from olibuguard.audit.records import DecisionAudit, EquityPoint


@runtime_checkable
class AuditSink(Protocol):
    def record_decision(self, audit: DecisionAudit) -> None: ...

    def record_equity(self, point: EquityPoint) -> None: ...


@runtime_checkable
class AuditReader(Protocol):
    """Read-only reconciliation interface; not implemented by NullAuditSink."""

    def peak_equity_quote(self) -> Decimal: ...

    def last_equity_point(self) -> EquityPoint | None: ...


class NullAuditSink:
    """Default sink: records nothing."""

    def record_decision(self, audit: DecisionAudit) -> None:
        return None

    def record_equity(self, point: EquityPoint) -> None:
        return None
