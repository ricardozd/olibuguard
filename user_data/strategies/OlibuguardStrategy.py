"""Thin adapter between Freqtrade and the olibuguard core.

The core (olibuguard.*) does not know Freqtrade. This class translates the
Freqtrade world (DataFrames, floats) into our domain types (Decimal, OrderIntent,
PortfolioState) and wires the risk gate as a second safety net:

  - populate_*_trend     -> signals (example EMA20/50 strategy, Phase 1)
  - custom_stake_amount  -> sizing: the risk gate caps the stake (% of capital)
  - confirm_trade_entry  -> final gate: AI veto → risk gate (slippage / limits / circuit breakers)

Phase 2 safety layers:
  A. Real equity/peak/PnL fed into circuit breakers.
  B. Audit DB (SQLiteAuditSink) records every decision and equity snapshot.
  C. Equity curve via bot_loop_start.
  D. File-based kill switch (KILL_SWITCH sentinel) checked before every entry.
  E. Reconciliation: peak equity restored from DB on startup so the drawdown
     circuit breaker is not reset to zero after a restart; equity drift is
     logged whenever it exceeds 5 % intra-candle.
  F. Robust error handling via run_safe + ErrorBudget: consecutive wallet-read
     failures automatically activate the kill switch after 5 misses.

All Freqtrade-specific access is confined to this adapter.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

from olibuguard.advisor.base import AIAdvisor, NullAdvisor, clamp_advisor_factor
from olibuguard.alerts.sink import AlertSink, NullAlertSink
from olibuguard.audit.records import DecisionAudit, EquityPoint
from olibuguard.audit.sink import AuditReader, AuditSink, NullAuditSink
from olibuguard.audit.version import code_version
from olibuguard.config import AppConfig, load_config
from olibuguard.domain.models import MarketContext, OrderIntent, PortfolioState, RiskVerdict, Side
from olibuguard.failsafe import ErrorBudget, run_safe
from olibuguard.kill_switch import KillSwitch
from olibuguard.reconciliation import check_equity_drift, restore_peak_equity
from olibuguard.risk.gate import RiskGate

logger = logging.getLogger(__name__)


class OlibuguardStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False
    stoploss = -0.10
    minimal_roi = {"0": 0.10}
    process_only_new_candles = True
    startup_candle_count = 50  # EMA50 needs 50 candles = ~12 h of 15m data

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
        self._risk_limits = config.risk   # kept for AI advisor context
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

        # Kill switch: sentinel file <user_data_dir>/KILL_SWITCH.
        user_data_dir_ks = Path(self.config.get("user_data_dir", "."))
        self._kill_switch = KillSwitch(user_data_dir_ks / "KILL_SWITCH")
        if self._kill_switch.is_active():
            logger.warning(
                "kill_switch_active_at_startup: path=%s", self._kill_switch.path
            )

        # Alert sink (G): try Telegram, fall back to no-op (fail-safe).
        alert: AlertSink = NullAlertSink()
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if token and chat_id:
            try:
                from olibuguard.alerts.telegram import TelegramAlertSink

                alert = TelegramAlertSink(token=token, chat_id=chat_id)
                logger.info("telegram_alerts_enabled: chat_id=%s", chat_id)
            except Exception as exc:
                logger.warning("telegram_alerts_unavailable: %s", exc)
        self._alert_sink: AlertSink = alert

        # Error budget (F): 5 consecutive wallet-read failures → kill switch + alert.
        self._equity_budget = ErrorBudget(
            "equity_read",
            max_consecutive=5,
            kill_switch=self._kill_switch,
            on_exhausted=lambda: self._alert(
                "OLIBUGUARD: KILL SWITCH ACTIVATED\n"
                "Reason: equity_read error budget exhausted (5 consecutive failures)\n"
                "Action: check exchange connectivity, then run: task resume"
            ),
        )

        # Reconciliation (E): restore peak equity from the audit DB so the drawdown
        # circuit breaker is not inadvertently reset to zero after a restart.
        if isinstance(self._audit, AuditReader):
            recorded_peak = run_safe(
                "audit_peak_read", self._audit.peak_equity_quote, Decimal("0")
            )
            current_equity = self._read_equity()
            self._peak_equity = restore_peak_equity(current_equity, recorded_peak)
            logger.info(
                "reconciliation.peak_restored: peak=%s current=%s",
                self._peak_equity,
                current_equity,
            )

        # AI advisor (Phase 3): load BedrockAdvisor if enabled, else NullAdvisor.
        advisor: AIAdvisor = NullAdvisor()
        if config.ai.enabled and config.ai.provider == "bedrock":
            try:
                from olibuguard.advisor.bedrock import BedrockAdvisor

                advisor = BedrockAdvisor(
                    model_id=config.ai.model,
                    region=config.ai.region,
                    profile=config.ai.profile,
                    max_tokens=config.ai.max_tokens,
                    thinking=config.ai.thinking,
                    thinking_budget_tokens=config.ai.thinking_budget_tokens,
                    timeout_seconds=config.ai.timeout_seconds,
                )
                logger.info("bedrock_advisor_started: model=%s", config.ai.model)
            except Exception as exc:
                logger.warning("bedrock_advisor_unavailable: %s — using NullAdvisor", exc)
        self._advisor: AIAdvisor = advisor

        # Startup alert (G).
        ks_status = "ACTIVE — no new entries" if self._kill_switch.is_active() else "inactive"
        self._alert(
            f"olibuguard started\n"
            f"version: {getattr(self, '_code_version', 'unknown')}\n"
            f"peak_equity: {self._peak_equity}\n"
            f"kill_switch: {ks_status}"
        )

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

    def _alerts(self) -> AlertSink:
        sink = getattr(self, "_alert_sink", None)
        if sink is None:
            sink = NullAlertSink()
            self._alert_sink = sink
        return sink

    def _ai(self) -> AIAdvisor:
        advisor = getattr(self, "_advisor", None)
        if advisor is None:
            advisor = NullAdvisor()
            self._advisor = advisor
        return advisor

    def _alert(self, message: str) -> None:
        """Send an alert notification; never raises (fail-safe)."""
        run_safe("alert_send", lambda: self._alerts().send(message), None)

    def _ks(self) -> KillSwitch:
        ks = getattr(self, "_kill_switch", None)
        if ks is None:
            # Fallback: sentinel in cwd (should only happen in unit tests).
            ks = KillSwitch(Path(".") / "KILL_SWITCH")
            self._kill_switch = ks
        return ks

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
        record = DecisionAudit(
            at=now.astimezone(UTC),
            symbol=symbol,
            kind=kind,
            reference_price=reference_price,
            equity_quote=equity,
            approved=verdict.approved,
            reason=verdict.reason,
            quote_amount=(verdict.intent.quote_amount if verdict.intent is not None else None),
            code_version=getattr(self, "_code_version", "unknown"),
        )
        run_safe("audit_decision", lambda: self._sink().record_decision(record), None)
        # Alert on circuit breaker trips (G).
        if not verdict.approved and verdict.reason.startswith("circuit breaker"):
            self._alert(
                f"OLIBUGUARD: CIRCUIT BREAKER TRIPPED\n"
                f"pair: {symbol}  kind: {kind}\n"
                f"reason: {verdict.reason}\n"
                f"equity: {equity}"
            )

    def bot_loop_start(self, current_time: datetime, **kwargs: Any) -> None:
        """Equity drift check every tick; snapshot written at most every 5 minutes (E+C).

        process_throttle_secs=5 would produce ~17k rows/day if we wrote every tick.
        The design intent (§5.7) is "periodic snapshot every N minutes".  The drift
        check is cheap and still runs on every call so we never miss a spike.
        """
        equity = self._read_equity()
        sink = self._sink()
        last = None

        if isinstance(sink, AuditReader):
            last = run_safe("audit_last_equity", sink.last_equity_point, None)
            # Drift check runs every tick regardless of the snapshot throttle.
            if last is not None:
                warning = check_equity_drift(last.equity_quote, equity)
                if warning:
                    logger.warning("reconciliation.%s", warning)
                    self._alert(f"OLIBUGUARD: EQUITY DRIFT\n{warning}")

        # Throttle: write at most once every 5 minutes.
        # If last is None (first run or non-readable sink) always record.
        now_utc = current_time.astimezone(UTC)
        should_record = last is None or (now_utc - last.at >= timedelta(minutes=5))
        if should_record:
            run_safe(
                "audit_equity",
                lambda: sink.record_equity(EquityPoint(at=now_utc, equity_quote=equity)),
                None,
            )

    def _read_equity(self) -> Decimal:
        wallets = self.wallets
        if wallets is None or not hasattr(wallets, "get_total_stake_amount"):
            return Decimal("0")
        return run_safe(
            "equity_read",
            lambda: Decimal(str(wallets.get_total_stake_amount())),
            Decimal("0"),
            budget=getattr(self, "_equity_budget", None),
        )

    def _realized_pnl_today(self, now: datetime) -> Decimal:
        start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return run_safe(
            "realized_pnl_read",
            lambda: sum(
                (
                    Decimal(str(t.close_profit_abs))
                    for t in Trade.get_trades_proxy(is_open=False, close_date=start)
                    if t.close_profit_abs is not None
                ),
                Decimal("0"),
            ),
            Decimal("0"),
        )

    def _portfolio_state(self, now: datetime) -> PortfolioState:
        equity = self._read_equity()
        peak = max(getattr(self, "_peak_equity", Decimal("0")), equity)
        self._peak_equity = peak
        return PortfolioState(
            equity_quote=equity,
            peak_equity_quote=peak,
            realized_pnl_today_quote=self._realized_pnl_today(now),
        )

    def _build_market_context(
        self,
        pair: str,
        price: Decimal,
        current_time: datetime,
        analyzed: DataFrame | None,
        state: PortfolioState,
    ) -> MarketContext:
        """Build a MarketContext for the AI advisor from live adapter data."""
        peak = state.peak_equity_quote
        drawdown = float((peak - state.equity_quote) / peak) if peak > 0 else 0.0

        # Risk-gate thresholds so the advisor knows how close we are to the limits.
        rl = getattr(self, "_risk_limits", None)

        indicators: dict[str, float] = {
            # Equity / drawdown
            "equity": float(state.equity_quote),
            "drawdown_pct": drawdown,
            # Portfolio composition
            "open_positions": float(state.open_positions),
            "open_exposure": float(state.open_exposure_quote),
            "realized_pnl_today": float(state.realized_pnl_today_quote),
            # Circuit-breaker limits (provide reference for proximity assessment)
            "daily_loss_limit_pct": float(rl.daily_loss_limit_pct) if rl else 0.05,
            "max_drawdown_pct": float(rl.max_drawdown_pct) if rl else 0.10,
            "max_open_positions": float(rl.max_open_positions) if rl else 3.0,
        }

        if analyzed is not None and not analyzed.empty:
            row = analyzed.iloc[-1]
            # Last-candle OHLCV + computed indicators
            for col in (
                "ema_fast", "ema_slow", "volume", "rsi", "volume_ratio",
                "open", "high", "low",
            ):
                if col in analyzed.columns:
                    indicators[col] = float(row[col])
            # Last 5 closes, newest first (close_0 = current close = price).
            tail = analyzed["close"].iloc[-5:].tolist()
            for i, c in enumerate(reversed(tail)):
                indicators[f"close_{i}"] = float(c)

        return MarketContext(
            symbol=pair,
            timestamp=current_time.astimezone(UTC),
            price=price,
            indicators=indicators,
        )

    def populate_indicators(self, dataframe: DataFrame, metadata: dict[str, Any]) -> DataFrame:
        dataframe["ema_fast"] = dataframe["close"].ewm(span=20, adjust=False).mean()
        dataframe["ema_slow"] = dataframe["close"].ewm(span=50, adjust=False).mean()

        # RSI 14 — Wilder's EWM method (pandas-native, no TA-Lib dependency).
        # clip(0,100) handles inf when there are no losses; fillna(50) handles startup NaN.
        delta = dataframe["close"].diff()
        gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
        dataframe["rsi"] = (100.0 - (100.0 / (1.0 + gain / loss))).clip(0.0, 100.0).fillna(50.0)

        # Volume ratio: current vs 20-candle rolling average.
        # where(>0) avoids division by zero; fillna(1.0) = "normal" when average unknown.
        vol_avg = dataframe["volume"].rolling(20, min_periods=1).mean()
        dataframe["volume_ratio"] = (dataframe["volume"] / vol_avg.where(vol_avg > 0)).fillna(1.0)

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
        ref_price = Decimal(str(current_rate))
        if self._ks().is_active():
            state = self._portfolio_state(current_time)
            self._audit_decision(
                kind="stake",
                symbol=pair,
                reference_price=ref_price,
                equity=state.equity_quote,
                verdict=RiskVerdict(approved=False, reason="kill switch active"),
                now=current_time,
            )
            logger.warning("kill_switch_blocked_stake: pair=%s", pair)
            return 0.0
        intent = OrderIntent(
            symbol=pair,
            side=Side.BUY,
            quote_amount=Decimal(str(proposed_stake)),
            reference_price=ref_price,
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
        analyzed: DataFrame | None = None
        dp = self.dp
        if dp is not None:
            analyzed, _ = dp.get_analyzed_dataframe(pair, self.timeframe)
            if analyzed is not None and not analyzed.empty:
                reference = Decimal(str(analyzed["close"].iloc[-1]))
        if self._ks().is_active():
            state = self._portfolio_state(current_time)
            self._audit_decision(
                kind="entry",
                symbol=pair,
                reference_price=reference,
                equity=state.equity_quote,
                verdict=RiskVerdict(approved=False, reason="kill switch active"),
                now=current_time,
            )
            logger.warning("kill_switch_blocked_entry: pair=%s", pair)
            return False
        # ── AI advisor veto (Phase 3) ─────────────────────────────────────────
        # The advisor can only reduce or block — never initiate or enlarge a trade.
        # Any error (network, boto3, JSON parse) returns None → trade proceeds.
        state = self._portfolio_state(current_time)
        ctx = self._build_market_context(pair, reference, current_time, analyzed, state)
        opinion = run_safe("advisor_opinion", lambda: self._ai().opinion(ctx), None)
        if opinion is not None:
            factor = clamp_advisor_factor(opinion.bias)
            if factor == 0.0:
                _ai_verdict = RiskVerdict(
                    approved=False, reason=f"ai_advisor: {opinion.rationale}"
                )
                self._audit_decision(
                    kind="entry",
                    symbol=pair,
                    reference_price=reference,
                    equity=state.equity_quote,
                    verdict=_ai_verdict,
                    now=current_time,
                )
                logger.warning(
                    "ai_advisor_veto: pair=%s reason=%s", pair, opinion.rationale
                )
                self._alert(
                    f"OLIBUGUARD: AI ADVISOR VETO\npair: {pair}\nreason: {opinion.rationale}"
                )
                return False
        intent = OrderIntent(
            symbol=pair,
            side=Side.BUY,
            quote_amount=Decimal(str(amount)) * Decimal(str(rate)),
            reference_price=reference,
            execution_price=Decimal(str(rate)),
        )
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
