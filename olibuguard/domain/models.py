"""Domain types (immutable value objects).

Money is always ``Decimal``. Timestamps are always tz-aware (stored in UTC).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class SignalAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


def side_for(action: SignalAction) -> Side | None:
    """Order side for a signal action. ``HOLD`` does not trade (``None``)."""
    if action is SignalAction.BUY:
        return Side.BUY
    if action is SignalAction.SELL:
        return Side.SELL
    return None


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketContext(_Frozen):
    """Market snapshot seen by the strategy and the advisor."""

    symbol: str
    timestamp: datetime
    price: Decimal = Field(gt=0)
    indicators: dict[str, float] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be tz-aware (UTC)")
        return value


class StrategySignal(_Frozen):
    """What the strategy PROPOSES. Knows nothing about the account or the limits.

    ``size_fraction`` is the fraction of the per-trade budget to use; the strategy
    never sees the absolute account size.
    """

    action: SignalAction
    symbol: str
    size_fraction: float = Field(default=1.0, gt=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = ""


class OrderIntent(_Frozen):
    """A concrete order intent, candidate to pass through the risk gate.

    ``reference_price`` is the signal price; ``execution_price`` (if known) is the
    price it would execute at. The gate vetoes when the slippage between the two
    exceeds the tolerated maximum.
    """

    symbol: str
    side: Side
    quote_amount: Decimal = Field(gt=0)
    reference_price: Decimal = Field(gt=0)
    execution_price: Decimal | None = Field(default=None, gt=0)
    reason: str = ""


class PortfolioState(_Frozen):
    """Portfolio state the risk gate needs to decide.

    Neutral defaults (equity/peak = 0) disable %-based sizing and the circuit
    breakers, leaving only the absolute caps: handy for tests and startup.
    """

    open_exposure_quote: Decimal = Field(default=Decimal("0"), ge=0)
    open_positions: int = Field(default=0, ge=0)
    orders_last_minute: int = Field(default=0, ge=0)
    equity_quote: Decimal = Field(default=Decimal("0"), ge=0)
    peak_equity_quote: Decimal = Field(default=Decimal("0"), ge=0)
    realized_pnl_today_quote: Decimal = Field(default=Decimal("0"))


class RiskVerdict(_Frozen):
    """Risk gate verdict. Approves, shrinks the size, or rejects."""

    approved: bool
    reason: str
    intent: OrderIntent | None = None
