"""Audit value objects (immutable). Money as Decimal, timestamps tz-aware (UTC)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class _AuditModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DecisionAudit(_AuditModel):
    """A single risk-gate decision, for post-mortem reproducibility (section 5.7)."""

    at: datetime
    symbol: str
    kind: str  # "stake" | "entry"
    reference_price: Decimal
    equity_quote: Decimal
    approved: bool
    reason: str
    quote_amount: Decimal | None = None
    code_version: str = "unknown"  # git commit SHA of the bot


class EquityPoint(_AuditModel):
    """A snapshot of account equity for the equity curve (section 6)."""

    at: datetime
    equity_quote: Decimal
