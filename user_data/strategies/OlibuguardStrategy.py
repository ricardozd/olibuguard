"""Thin adapter between Freqtrade and the olibuguard core.

The core (olibuguard.*) does not know Freqtrade. This class translates the
Freqtrade world (DataFrames, floats) into our domain types (Decimal, OrderIntent,
PortfolioState) and wires the risk gate as a second safety net:

  - populate_*_trend     -> signals (example EMA20/50 strategy, Phase 1)
  - custom_stake_amount  -> sizing: the risk gate caps the stake (% of capital)
  - confirm_trade_entry  -> final gate: veto by slippage / limits / circuit breakers

Portfolio state fed to the gate (Phase 2, increments A–C): real equity from wallets,
peak equity tracked in-memory and persisted to the audit DB, today's realized PnL
from closed trades, and a periodic equity-curve snapshot via bot_loop_start.
Reads are defensive: on failure we log and fall back to neutral values, never
blocking trading on a read error (fail-safe). Audit failures also never block
trading (fail-safe). All Freqtrade-specific access is confined to this adapter.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

from olibuguard.audit.records import DecisionAudit, EquityPoint
from olibuguard.audit.sink import AuditSink, NullAuditSink
from olibuguard.audit.version import code_version
from olibuguard.config import AppConfig, load_config
from olibuguard.domain.models import OrderIntent, PortfolioState, Side
from olibuguard.risk.gate import RiskGate, RiskVerdict

logger = logging.getLogger(__name__)


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
        # Kill-switch at the Freqtrade level (second net over the gate circuit breakers).
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
        self._peak_equity = Decimal("0")
        self._code_version = code_version()

        # Audit sink: try the SQLite backend, fall back to no-op (fail-safe).
        audit: AuditSink = NullAuditSink()
        try:
            from olibuguard.audit.sqlite import SQLiteAuditSink

            user_data_dir = Path(self.config.get("user_data_dir", "."))
            audit = SQLiteAuditSink(user_data_dir / "olibuguard_audit.sqlite")
            logger.info("audit_sink_started: path=%s", user_data_dir / "olibuguard_audit.sqlite")
        except Exception as exc:
            logger.warning("audit_sink_unavailable: %s — using NullAuditSink", exc)
        self._audit: AuditSink = audit

    def _gate(self) -> RiskGate:
        gate = getattr(self, "_risk", None)
        if gate is None:
            gate = RiskGate(AppConfig().risk)
            self._risk = gate
        return gate

    def _sink(self) -> AuditSink:
        sink = getattr(self, "_audit", None)
        if sink is None:
            sink = NullAuditSink()
            self._audit = sink
        return sink

    def _audit_decision(
        self,
        kind: str,
        symbol: str,
        reference_price: Decimal,
        equity: Decimal,
        verdict: RiskVerdict,
        now: datetime,
    ) -> None:
        """Persist a risk-gate decision; never raises (fail-safe)."""
        try:
            self._sink().record_decision(
                DecisionAudit(
                    at=now.astimezone(UTC),
                    symbol=symbol,
                    kind=kind,
                    reference_price=reference_price,
                    equity_quote=equity,
                    approved=verdict.approved,
                    reason=verdict.reason,
                    quote_amount=(
                        verdict.intent.quote_amount if verdict.intent is not None else None
                    ),
                    code_version=getattr(self, "_code_version", "unknown"),
                )
            )
        except Exception as exc:  # audit must never block trading
            logger.warning("audit_decision_failed: %s", exc)

    def bot_loop_start(self, current_time: datetime, **kwargs: Any) -> None:
        """Record an equity snapshot for the equity curve (increment C)."""
        try:
            equity = self._read_equity()
            self._sink().record_equity(
                EquityPoint(at=current_time.astimezone(UTC), equity_quote=equity)
            )
        except Exception as exc:  # audit must never block trading
            logger.warning("audit_equity_failed: %s", exc)

    def _read_equity(self) -> Decimal:
        wallets = self.wallets
        if wallets is not None and hasattr(wallets, "get_total_stake_amount"):
            try:
                return Decimal(str(wallets.get_total_stake_amount()))
            except Exception as exc:  # fail-safe: never block trading on a read error
                logger.warning("equity_read_failed: %s", exc)
        return Decimal("0")

    def _realized_pnl_today(self, now: datetime) -> Decimal:
        try:
            start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            closed = Trade.get_trades_proxy(is_open=False, close_date=start)
            return sum(
                (Decimal(str(t.close_profit_abs)) for t in closed if t.close_profit_abs is not None),
                Decimal("0"),
            )
        except Exception as exc:  # fail-safe: a read failure must not halt trading
            logger.warning("realized_pnl_read_failed: %s", exc)
            return Decimal("0")

    def _portfolio_state(self, now: datetime) -> PortfolioState:
        equity = self._read_equity()
        peak = max(getattr(self, "_peak_equity", Decimal("0")), equity)
        self._peak_equity = peak
        return PortfolioState(
            equity_quote=equity,
            peak_equity_quote=peak,
            realized_pnl_today_quote=self._realized_pnl_today(now),
        )

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
        state = self._portfolio_state(current_time)
        verdict = self._gate().evaluate(intent, state)
        self._audit_decision(
            kind="stake",
            symbol=pair,
            reference_price=Decimal(str(current_rate)),
            equity=state.equity_quote,
            verdict=verdict,
            now=current_time,
        )
        if not verdict.approved or verdict.intent is None:
            return 0.0
        approved = float(verdict.intent.quote_amount)
        if min_stake is not None and approved < min_stake:
            return 0.0  # below the exchange minimum => Freqtrade does not enter
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
        state = self._portfolio_state(current_time)
        verdict = self._gate().evaluate(intent, state)
        self._audit_decision(
            kind="entry",
            symbol=pair,
            reference_price=reference,
            equity=state.equity_quote,
            verdict=verdict,
            now=current_time,
        )
        return verdict.approved
