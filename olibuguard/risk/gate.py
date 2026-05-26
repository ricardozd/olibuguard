"""Risk gate: el módulo invariante. La estrategia propone, el risk gate dispone.

Orden de evaluación (fail-safe; corta en la primera infracción):
  1. Circuit breakers (kill-switch): drawdown desde pico y pérdida diaria.
  2. Whitelist / blacklist de pares.
  3. Anti-runaway: tasa de órdenes por minuto.
  4. Slippage entre precio de señal y de ejecución.
  5. Sizing: % del capital + caps absolutos + exposición disponible.
  6. Mínimo nocional.

Puede rechazar o REDUCIR el tamaño, nunca agrandarlo.
"""

from __future__ import annotations

from decimal import Decimal

from olibuguard.config import RiskLimits
from olibuguard.domain.models import OrderIntent, PortfolioState, RiskVerdict, Side


def _reject(reason: str) -> RiskVerdict:
    return RiskVerdict(approved=False, reason=reason, intent=None)


def _pct(value: float) -> Decimal:
    return Decimal(str(value))


class RiskGate:
    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def evaluate(self, intent: OrderIntent, state: PortfolioState) -> RiskVerdict:
        breaker = self._tripped_breaker(state)
        if breaker is not None:
            return _reject(breaker)

        limits = self._limits

        if intent.symbol in limits.blacklist:
            return _reject(f"par {intent.symbol} en blacklist")
        if limits.whitelist and intent.symbol not in limits.whitelist:
            return _reject(f"par {intent.symbol} fuera de whitelist")

        if state.orders_last_minute >= limits.max_orders_per_minute:
            return _reject("límite de órdenes por minuto alcanzado")

        if intent.execution_price is not None and intent.reference_price > 0:
            slippage = abs(intent.execution_price - intent.reference_price) / intent.reference_price
            if slippage > _pct(limits.max_slippage_pct):
                return _reject(
                    f"slippage {slippage:.4%} supera el máximo {limits.max_slippage_pct:.4%}"
                )

        amount = self._cap_size(intent, state)
        if intent.side is Side.BUY:
            if state.open_positions >= limits.max_open_positions:
                return _reject("número máximo de posiciones abiertas alcanzado")
            available = limits.max_total_exposure_quote - state.open_exposure_quote
            if available <= 0:
                return _reject("exposición total máxima alcanzada")
            amount = min(amount, available)

        if amount < limits.min_order_quote:
            return _reject(
                f"tamaño {amount} por debajo del mínimo nocional {limits.min_order_quote}"
            )

        if amount == intent.quote_amount:
            return RiskVerdict(approved=True, reason="aprobada", intent=intent)
        final = intent.model_copy(update={"quote_amount": amount})
        return RiskVerdict(
            approved=True, reason=f"aprobada con tamaño reducido a {amount}", intent=final
        )

    def _tripped_breaker(self, state: PortfolioState) -> str | None:
        limits = self._limits
        if state.peak_equity_quote > 0:
            drawdown = (state.peak_equity_quote - state.equity_quote) / state.peak_equity_quote
            if drawdown >= _pct(limits.max_drawdown_pct):
                return f"circuit breaker: drawdown {drawdown:.2%} >= {limits.max_drawdown_pct:.2%}"
        if state.equity_quote > 0:
            daily_limit = state.equity_quote * _pct(limits.daily_loss_limit_pct)
            if state.realized_pnl_today_quote <= -daily_limit:
                return (
                    f"circuit breaker: pérdida diaria {state.realized_pnl_today_quote} "
                    f"alcanza el límite -{daily_limit}"
                )
        return None

    def _cap_size(self, intent: OrderIntent, state: PortfolioState) -> Decimal:
        amount = intent.quote_amount
        if state.equity_quote > 0:
            risk_cap = state.equity_quote * _pct(self._limits.max_risk_per_trade_pct)
            amount = min(amount, risk_cap)
        return min(amount, self._limits.max_position_quote)
