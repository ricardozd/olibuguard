"""Adaptador fino entre Freqtrade y el núcleo olibuguard.

El núcleo (olibuguard.*) NO conoce Freqtrade. Esta clase traduce el mundo de
Freqtrade (DataFrames, floats) a nuestros tipos de dominio (Decimal, OrderIntent,
PortfolioState) y engancha el risk gate como segunda red de seguridad:

  - populate_*_trend     -> señales (estrategia EMA20/50 de ejemplo, Fase 1)
  - custom_stake_amount  -> dimensionado: el risk gate capa el stake (% del capital)
  - confirm_trade_entry  -> última compuerta: veto por slippage / límites

El equity real se lee de self.wallets para el sizing por % del capital. El P&L
diario y el pico de equity (para los circuit breakers del gate) se afinan en Fase 2;
mientras tanto el kill-switch en vivo lo cubren las `protections` de Freqtrade
(MaxDrawdown/StoplossGuard) configuradas en user_data/config.json.
"""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pandas import DataFrame

from freqtrade.strategy import IStrategy

from olibuguard.config import AppConfig, load_config
from olibuguard.domain.models import OrderIntent, PortfolioState, Side
from olibuguard.risk.gate import RiskGate


class OlibuguardStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    stoploss = -0.10
    minimal_roi = {"0": 0.10}
    process_only_new_candles = True
    startup_candle_count = 50

    @property
    def protections(self) -> list[dict[str, Any]]:
        # Kill-switch a nivel Freqtrade (segunda red sobre los circuit breakers del gate).
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 48,
                "trade_limit": 20,
                "stop_duration_candles": 12,
                "max_allowed_drawdown": 0.10,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 4,
                "stop_duration_candles": 12,
                "only_per_pair": False,
            },
        ]

    def bot_start(self, **kwargs: Any) -> None:
        cfg_path = os.environ.get("OLIBUGUARD_CONFIG")
        config = load_config(Path(cfg_path)) if cfg_path else AppConfig()
        self._risk = RiskGate(config.risk)

    def _gate(self) -> RiskGate:
        gate = getattr(self, "_risk", None)
        if gate is None:
            gate = RiskGate(AppConfig().risk)
            self._risk = gate
        return gate

    def _portfolio_state(self) -> PortfolioState:
        equity = Decimal("0")
        wallets = self.wallets
        if wallets is not None and hasattr(wallets, "get_total_stake_amount"):
            equity = Decimal(str(wallets.get_total_stake_amount()))
        return PortfolioState(equity_quote=equity)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict[str, Any]) -> DataFrame:
        dataframe["ema_fast"] = dataframe["close"].ewm(span=20, adjust=False).mean()
        dataframe["ema_slow"] = dataframe["close"].ewm(span=50, adjust=False).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict[str, Any]) -> DataFrame:
        fast, slow = dataframe["ema_fast"], dataframe["ema_slow"]
        crossed_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        dataframe.loc[crossed_up & (dataframe["volume"] > 0), "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict[str, Any]) -> DataFrame:
        fast, slow = dataframe["ema_fast"], dataframe["ema_slow"]
        crossed_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        dataframe.loc[crossed_down, "exit_long"] = 1
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float:
        if proposed_stake <= 0:
            return 0.0
        intent = OrderIntent(
            symbol=pair,
            side=Side.BUY,
            quote_amount=Decimal(str(proposed_stake)),
            reference_price=Decimal(str(current_rate)),
        )
        verdict = self._gate().evaluate(intent, self._portfolio_state())
        if not verdict.approved or verdict.intent is None:
            return 0.0
        approved = float(verdict.intent.quote_amount)
        if min_stake is not None and approved < min_stake:
            return 0.0  # por debajo del mínimo del exchange => Freqtrade no entra
        return min(approved, max_stake)

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> bool:
        reference = Decimal(str(rate))
        dp = self.dp
        if dp is not None:
            analyzed, _ = dp.get_analyzed_dataframe(pair, self.timeframe)
            if analyzed is not None and not analyzed.empty:
                reference = Decimal(str(analyzed["close"].iloc[-1]))
        intent = OrderIntent(
            symbol=pair,
            side=Side.BUY,
            quote_amount=Decimal(str(amount)) * Decimal(str(rate)),
            reference_price=reference,
            execution_price=Decimal(str(rate)),
        )
        return self._gate().evaluate(intent, self._portfolio_state()).approved
