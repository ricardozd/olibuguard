from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from olibuguard.config import RiskLimits
from olibuguard.domain.models import (
    MarketContext,
    OrderIntent,
    PortfolioState,
    SignalAction,
    StrategySignal,
)
from olibuguard.orchestrator import Orchestrator
from olibuguard.risk.gate import RiskGate


class FakeMarketData:
    def latest(self, symbol: str) -> MarketContext:
        return MarketContext(symbol=symbol, timestamp=datetime.now(UTC), price=Decimal("100"))


class FixedStrategy:
    def __init__(self, action: SignalAction) -> None:
        self._action = action

    def decide(self, context: MarketContext) -> StrategySignal:
        return StrategySignal(action=self._action, symbol=context.symbol, size_fraction=1.0)


class RecordingOrderManager:
    def __init__(self) -> None:
        self.submitted: list[OrderIntent] = []

    def submit(self, intent: OrderIntent) -> None:
        self.submitted.append(intent)


def _gate() -> RiskGate:
    return RiskGate(RiskLimits(whitelist=["BTC/USDT"], blacklist=[]))


def test_hold_submits_nothing() -> None:
    om = RecordingOrderManager()
    orch = Orchestrator(
        FakeMarketData(), FixedStrategy(SignalAction.HOLD), _gate(), om, Decimal("50")
    )
    assert orch.tick("BTC/USDT", PortfolioState()) is None
    assert om.submitted == []


def test_buy_passes_gate_and_submits() -> None:
    om = RecordingOrderManager()
    orch = Orchestrator(
        FakeMarketData(), FixedStrategy(SignalAction.BUY), _gate(), om, Decimal("50")
    )
    verdict = orch.tick("BTC/USDT", PortfolioState())
    assert verdict is not None
    assert verdict.approved
    assert len(om.submitted) == 1
    assert om.submitted[0].quote_amount == Decimal("50")
