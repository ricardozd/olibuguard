"""Ports (interfaces) of the hexagonal core.

The orchestrator talks only to these Protocols; concrete adapters (ccxt, SQLite,
etc.) implement them in the outer layers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from olibuguard.domain.models import (
    MarketContext,
    OrderIntent,
    PortfolioState,
    RiskVerdict,
    StrategySignal,
)


@runtime_checkable
class MarketDataPort(Protocol):
    def latest(self, symbol: str) -> MarketContext: ...


@runtime_checkable
class StrategyPort(Protocol):
    def decide(self, context: MarketContext) -> StrategySignal: ...


@runtime_checkable
class RiskGatePort(Protocol):
    def evaluate(self, intent: OrderIntent, state: PortfolioState) -> RiskVerdict: ...


@runtime_checkable
class OrderManagerPort(Protocol):
    def submit(self, intent: OrderIntent) -> None: ...
