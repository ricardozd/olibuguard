"""Tipos de dominio (value objects inmutables).

Dinero siempre en ``Decimal``. Timestamps siempre tz-aware (almacenamiento UTC).
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
    """Lado de orden para una acción de señal. ``HOLD`` no opera (``None``)."""
    if action is SignalAction.BUY:
        return Side.BUY
    if action is SignalAction.SELL:
        return Side.SELL
    return None


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketContext(_Frozen):
    """Foto de mercado que ven la estrategia y el advisor."""

    symbol: str
    timestamp: datetime
    price: Decimal = Field(gt=0)
    indicators: dict[str, float] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp debe ser tz-aware (UTC)")
        return value


class StrategySignal(_Frozen):
    """Lo que la estrategia PROPONE. No conoce la cuenta ni los límites.

    ``size_fraction`` es la fracción del presupuesto por operación a usar; la
    estrategia nunca ve el tamaño absoluto de la cuenta.
    """

    action: SignalAction
    symbol: str
    size_fraction: float = Field(default=1.0, gt=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = ""


class OrderIntent(_Frozen):
    """Intención de orden concreta, candidata a pasar por el risk gate.

    ``reference_price`` es el precio de la señal; ``execution_price`` (si se conoce)
    es el precio al que se ejecutaría. El gate veta si el slippage entre ambos supera
    el máximo tolerado.
    """

    symbol: str
    side: Side
    quote_amount: Decimal = Field(gt=0)
    reference_price: Decimal = Field(gt=0)
    execution_price: Decimal | None = Field(default=None, gt=0)
    reason: str = ""


class PortfolioState(_Frozen):
    """Estado de cartera que el risk gate necesita para decidir.

    Valores neutros por defecto (equity/peak = 0) desactivan el sizing por % y los
    circuit breakers, dejando solo los caps absolutos: útil para tests y arranque.
    """

    open_exposure_quote: Decimal = Field(default=Decimal("0"), ge=0)
    open_positions: int = Field(default=0, ge=0)
    orders_last_minute: int = Field(default=0, ge=0)
    equity_quote: Decimal = Field(default=Decimal("0"), ge=0)
    peak_equity_quote: Decimal = Field(default=Decimal("0"), ge=0)
    realized_pnl_today_quote: Decimal = Field(default=Decimal("0"))


class RiskVerdict(_Frozen):
    """Veredicto del risk gate. Aprueba, reduce el tamaño o rechaza."""

    approved: bool
    reason: str
    intent: OrderIntent | None = None
