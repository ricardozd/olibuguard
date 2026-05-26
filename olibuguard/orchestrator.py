"""Orchestrator: the decision loop.

tick -> market data -> strategy -> risk gate -> order manager. Talks only to
ports; knows nothing about concrete adapters. Real sizing arrives in Phase 1;
here a fixed per-trade budget is applied to the fraction the signal requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from olibuguard.domain.models import (
    OrderIntent,
    PortfolioState,
    RiskVerdict,
    side_for,
)
from olibuguard.domain.ports import (
    MarketDataPort,
    OrderManagerPort,
    RiskGatePort,
    StrategyPort,
)
from olibuguard.logging import get_logger

log = get_logger("orchestrator")


@dataclass(slots=True)
class Orchestrator:
    market_data: MarketDataPort
    strategy: StrategyPort
    risk_gate: RiskGatePort
    order_manager: OrderManagerPort
    per_trade_budget_quote: Decimal

    def tick(self, symbol: str, state: PortfolioState) -> RiskVerdict | None:
        context = self.market_data.latest(symbol)
        signal = self.strategy.decide(context)

        side = side_for(signal.action)
        if side is None:
            log.info("signal.hold", symbol=symbol)
            return None

        amount = self.per_trade_budget_quote * Decimal(str(signal.size_fraction))
        intent = OrderIntent(
            symbol=symbol,
            side=side,
            quote_amount=amount,
            reference_price=context.price,
            reason=signal.reason,
        )
        verdict = self.risk_gate.evaluate(intent, state)
        log.info("risk.verdict", symbol=symbol, approved=verdict.approved, reason=verdict.reason)
        if verdict.approved and verdict.intent is not None:
            self.order_manager.submit(verdict.intent)
        return verdict
