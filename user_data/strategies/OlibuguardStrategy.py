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

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    merge_informative_pair,
    stoploss_from_absolute,
)

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
    can_short = True           # futures: Golden Cross = long, Death Cross = short
    stoploss = -0.10          # hard floor: Freqtrade enforces this even if ATR says wider
    use_custom_stoploss = True
    minimal_roi = {"0": 0.07}   # 7% — sweet spot found via ROI sweep (5%→-1.08, 7%→-0.50, 10%→-1.04)
    process_only_new_candles = True
    # 200 candles per timeframe: covers EMA50 on 15m and EMA200 on 1h.
    startup_candle_count = 200

    # ATR stoploss multipliers — reference values; the actual values used at runtime
    # come from the DecimalParameter objects below (supports Hyperopt).
    ATR_MULTIPLIER    = 3.5   # trend default
    BREAKOUT_ATR_MULT = 2.5   # breakout default (signals disabled — kept for future use)
    RANGE_ATR_MULT    = 2.0   # mean-reversion default

    # ── Hyperopt search spaces ───────────────────────────────────────────────
    # ATR multiplier for trend signals (ema_gc / ema_dc).
    # Low → tight stop, cut losses fast but exit good trends early.
    # High → wide stop, let trends breathe but absorb bigger individual losses.
    # Range extended to 10.0: previous hyperopt used broken circuit-breaker data
    # that only counted 2022 bear-market trades, biasing toward tight stops.
    # Full 4-year dataset (2022-2025) needs room to find the wider optimum.
    atr_trend_mult = DecimalParameter(1.5, 10.0, default=3.5, space="buy", optimize=True, load=True)

    # ATR multiplier for mean-reversion entries (rsi_bounce / rsi_drop).
    # Range signals are currently disabled — this parameter is kept for
    # potential re-enablement but excluded from hyperopt search.
    atr_range_mult = DecimalParameter(1.0, 3.5, default=2.0, space="buy", optimize=False, load=True)

    # Break-even lock: once this profit % is reached, stop moves to entry price.
    # Lower → lock in gains sooner (good for whipsaw markets).
    # Higher → let trade breathe more (good for smooth trends).
    breakeven_pct = DecimalParameter(0.005, 0.05, default=0.02, space="buy", optimize=True, load=True)

    # RSI ceiling for Golden Cross long entries — skip overbought candles.
    rsi_long_max = IntParameter(50, 80, default=65, space="buy", optimize=True, load=True)

    # RSI floor for Death Cross short entries — skip already-oversold candles.
    rsi_short_min = IntParameter(20, 50, default=35, space="sell", optimize=True, load=True)

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

    def informative_pairs(self) -> list[tuple[str, str]]:
        """Declare 1h and 4h data for every whitelisted pair.

        - 1h: EMA50/200 crossover signals + macro level filter (close vs EMA200).
        - 4h: EMA200 slope filter (macro trend DIRECTION over the last 2 days).
        """
        pairs = self.dp.current_whitelist() if self.dp else []
        return [(pair, "1h") for pair in pairs] + [(pair, "4h") for pair in pairs]

    def bot_start(self, **kwargs: Any) -> None:
        cfg_path = os.environ.get("OLIBUGUARD_CONFIG")
        config = load_config(Path(cfg_path)) if cfg_path else AppConfig()
        self._risk = RiskGate(config.risk)
        self._risk_limits = config.risk   # kept for AI advisor context
        self._peak_equity = Decimal("0")
        self._code_version = code_version()

        # Audit sink: try the SQLite backend, fall back to no-op (fail-safe).
        # The DB path defaults to <user_data_dir>/olibuguard_audit.sqlite but can be
        # overridden via OLIBUGUARD_AUDIT_DB — used by the historical-replay validation
        # so it writes to a separate file instead of polluting (or locking) the live
        # paper-trading audit DB.
        audit: AuditSink = NullAuditSink()
        try:
            from olibuguard.audit.sqlite import SQLiteAuditSink

            user_data_dir = Path(self.config.get("user_data_dir", "."))
            audit_path = os.environ.get("OLIBUGUARD_AUDIT_DB") or str(
                user_data_dir / "olibuguard_audit.sqlite"
            )
            audit = SQLiteAuditSink(Path(audit_path))
            logger.info("audit_sink_started: path=%s", audit_path)
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
        # Guard: skip in backtest/hyperopt — the DB holds LIVE equity history; restoring
        # a stale live peak into a simulation would cause false drawdown readings that
        # block all simulated trades.
        if self._is_live and isinstance(self._audit, AuditReader):
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

    @property
    def _is_live(self) -> bool:
        """True only in live/paper/dry-run modes — never in backtest or hyperopt."""
        return self.config.get("runmode", RunMode.OTHER) in (
            RunMode.LIVE,
            RunMode.DRY_RUN,
        )

    @property
    def _advisor_in_backtest(self) -> bool:
        """One-time validation switch (env OLIBUGUARD_ADVISOR_IN_BACKTEST=1).

        Lets the Bedrock advisor run AND its risk-gate decisions be recorded during
        backtest, so the full AI decision pipeline (signal → context → advisor veto →
        verdict) can be validated over historical signals without waiting for live
        trades.  Crucially, this lets us later JOIN each AI veto against the actual
        historical outcome of that trade.

        Deliberately does NOT enable circuit breakers (peak stays 0 in backtest via
        _portfolio_state) or Telegram alerts (gated on _is_live) — only the advisor
        call and the decision-audit write.  Off by default so hyperopt, which runs
        thousands of backtests, never incurs AWS cost.
        """
        return os.environ.get("OLIBUGUARD_ADVISOR_IN_BACKTEST", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    @property
    def _record_decisions(self) -> bool:
        """Whether risk-gate / advisor decisions should be persisted to the audit DB."""
        return self._is_live or self._advisor_in_backtest

    def _alert(self, message: str) -> None:
        """Send an alert notification; never raises (fail-safe).

        Silenced in backtesting and hyperopt to avoid flooding Telegram
        with every strategy evaluation run.
        """
        if not self._is_live:
            return
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
        """Persist a risk-gate decision; never raises (fail-safe).

        DB writes are skipped during backtests and hyperopt so simulated decisions
        never pollute the live audit trail — UNLESS the one-time advisor-validation
        switch (_advisor_in_backtest) is on, in which case historical decisions are
        recorded so the AI pipeline can be audited.  The circuit-breaker alert is
        gated via _alert → _is_live, so nothing leaks to Telegram regardless.
        """
        if self._record_decisions:
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
        # Alert on circuit breaker trips (G). _alert is already gated on _is_live.
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

        All DB writes are skipped in backtest/hyperopt so simulated equity never
        pollutes the live audit trail.
        """
        if not self._is_live:
            return  # nothing to record outside of live/dry-run

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
        # Circuit breakers (drawdown + daily loss) are live-trading safety nets.
        # In backtest/hyperopt the wallet API reports simulated equity that includes
        # unrealized open-position losses, which can push the apparent drawdown above
        # the threshold and trip the breaker permanently — blocking all future entries
        # and producing misleading backtesting results.  Peak and daily PnL are set to
        # neutral values so the risk gate applies sizing and min-notional checks only.
        if self._is_live:
            peak = max(getattr(self, "_peak_equity", Decimal("0")), equity)
            self._peak_equity = peak
            realized_pnl = self._realized_pnl_today(now)
        else:
            peak = Decimal("0")   # disables drawdown circuit breaker
            realized_pnl = Decimal("0")  # disables daily-loss circuit breaker
        return PortfolioState(
            equity_quote=equity,
            peak_equity_quote=peak,
            realized_pnl_today_quote=realized_pnl,
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
        # Golden Cross: EMA 50 (fast) / EMA 200 (slow) on 15m
        # EMA 50  = ~12.5 h trend · EMA 200 = ~50 h trend — far fewer false signals than 20/50.
        dataframe["ema_fast"] = dataframe["close"].ewm(span=50, adjust=False).mean()
        dataframe["ema_slow"] = dataframe["close"].ewm(span=200, adjust=False).mean()

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

        # ATR 14 — Wilder's EWM (pandas-native, no TA-Lib).
        # True Range = max(high-low, |high-prev_close|, |low-prev_close|)
        prev_close = dataframe["close"].shift(1)
        tr = (
            (dataframe["high"] - dataframe["low"])
            .combine(abs(dataframe["high"] - prev_close), max)
            .combine(abs(dataframe["low"] - prev_close), max)
        )
        dataframe["atr"] = tr.ewm(com=13, adjust=False).mean()

        # ADX (Average Directional Index) — measures trend STRENGTH, not direction.
        # ADX > 25 = trending market (EMA crossover valid).
        # ADX < 20 = ranging market (mean-reversion valid).
        # Built from the same True Range components as ATR (Wilder's method, pandas-native).
        prev_high = dataframe["high"].shift(1)
        prev_low = dataframe["low"].shift(1)
        dm_pos_raw = dataframe["high"] - prev_high
        dm_neg_raw = prev_low - dataframe["low"]
        # Positive DM wins only when it exceeds negative DM (and is positive).
        dm_pos = dm_pos_raw.where((dm_pos_raw > dm_neg_raw) & (dm_pos_raw > 0), 0.0)
        dm_neg = dm_neg_raw.where((dm_neg_raw > dm_pos_raw) & (dm_neg_raw > 0), 0.0)
        dm_pos_s = dm_pos.ewm(com=13, adjust=False).mean()
        dm_neg_s = dm_neg.ewm(com=13, adjust=False).mean()
        atr_safe = dataframe["atr"].replace(0, 1e-9)  # guard against div-by-zero at startup
        di_pos = 100 * dm_pos_s / atr_safe
        di_neg = 100 * dm_neg_s / atr_safe
        di_sum = (di_pos + di_neg).replace(0, 1e-9)
        dx = 100 * (di_pos - di_neg).abs() / di_sum
        dataframe["adx"] = dx.ewm(com=13, adjust=False).mean().fillna(25.0)

        # Bollinger Bands (20-period, 2σ) — used for mean-reversion entries.
        bb_mid = dataframe["close"].rolling(20, min_periods=1).mean()
        bb_std = dataframe["close"].rolling(20, min_periods=1).std(ddof=0).fillna(0)
        dataframe["bb_upper"] = bb_mid + 2 * bb_std
        dataframe["bb_lower"] = bb_mid - 2 * bb_std
        dataframe["bb_mid"] = bb_mid

        # Rolling 20-candle high/low — used for breakout entries.
        # shift(1) so the breakout candle does not count itself.
        dataframe["roll_high"] = dataframe["high"].rolling(20, min_periods=1).max().shift(1)
        dataframe["roll_low"] = dataframe["low"].rolling(20, min_periods=1).min().shift(1)

        # 1h trend filter: EMA 200 on 1-hour candles.
        # Merges the closest 1h candle into each 5m row (forward-fill).
        # Falls back gracefully: if 1h data is unavailable the column stays NaN
        # and populate_entry_trend treats it as "filter disabled" (fail-safe).
        if self.dp is not None:
            inf_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1h")
            if not inf_1h.empty:
                inf_1h = inf_1h.copy()
                # EMA50 + EMA200 on 1h: trend crossovers at this timeframe span 50h/200h
                # of price history — far fewer false signals than the same EMAs on 15m
                # (12h/50h).  merge_informative_pair forward-fills into 15m rows so each
                # 1h candle covers exactly 4 rows; shift(1) still detects the crossover
                # only on the first 15m row of the new 1h period.
                inf_1h["ema50"]  = inf_1h["close"].ewm(span=50,  adjust=False).mean()
                inf_1h["ema200"] = inf_1h["close"].ewm(span=200, adjust=False).mean()
                dataframe = merge_informative_pair(
                    dataframe, inf_1h, self.timeframe, "1h", ffill=True
                )

        # 4h macro slope filter: EMA 200 on 4-hour candles + 2-day slope.
        # Spans 200×4h = 800h ≈ 33 days — truly independent of the 1h crossover signal.
        # The SLOPE (current EMA200 vs 12 bars ago = 48h ago) captures macro trend
        # direction: a flat / reversing slope filters out chop-driven fakeouts.
        # Diagnostic showed 2025 had 19.5 % fakeouts vs 12.5 % in 2024 because the
        # 4h macro trend oscillated (46 % up / 54 % down) instead of staying directional.
        # NOTE: a previous attempt used 4h EMA200 as a LEVEL filter (close > ema200)
        # and made results worse; slope is a fundamentally different signal.
        if self.dp is not None:
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
            if not inf_4h.empty:
                inf_4h = inf_4h.copy()
                inf_4h["ema200"] = inf_4h["close"].ewm(span=200, adjust=False).mean()
                inf_4h["ema200_slope"] = inf_4h["ema200"] - inf_4h["ema200"].shift(12)
                dataframe = merge_informative_pair(
                    dataframe, inf_4h, self.timeframe, "4h", ffill=True
                )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict[str, Any]) -> DataFrame:
        """Trend-following entry engine — two signals tagged via enter_tag.

          1 · ema_gc  Golden Cross long  — EMA50(1h) crosses above EMA200(1h), price above
                      macro EMA200(1h), RSI not overbought
          2 · ema_dc  Death Cross short — EMA50(1h) crosses below EMA200(1h), price below
                      macro EMA200(1h), RSI not oversold

        Mean-reversion signals (rsi_bounce / rsi_drop) were disabled after backtesting
        showed they were consistently net-negative over 4 years across all market regimes:
        they fired at the wrong point in the trend and their ATR stops were too tight.

        GC/DC have NO ADX gate: EMA crossovers fire as a trend is forming, before ADX
        has time to build — requiring ADX ≥ 25 cuts >97% of valid signals.
        """
        volume_ok = dataframe["volume"] > 0

        # ── 1h macro trend filter (LEVEL: price vs 1h EMA200) ──────────────────
        # Falls back to True (filter disabled) when 1h data is NaN — fail-safe.
        if "ema200_1h" in dataframe.columns:
            ema_available = dataframe["ema200_1h"].notna()
            macro_up   = (dataframe["close"] > dataframe["ema200_1h"]) | ~ema_available
            macro_down = (dataframe["close"] < dataframe["ema200_1h"]) | ~ema_available
        else:
            macro_up   = dataframe["close"] > 0   # always True — fail-safe
            macro_down = dataframe["close"] > 0   # always True — fail-safe

        # ── 4h macro slope filter (DIRECTION: 2-day slope of 4h EMA200) ─────────
        # Blocks longs when the macro trend is flat / reversing (chop), blocks
        # shorts when the macro trend is rising.  Targets the 2025 regime where
        # the 4h EMA200 oscillated 46 %/54 % up/down vs ~65 % in 2023-2024.
        # Fail-safe: if 4h data is NaN, the filter is disabled.
        if "ema200_slope_4h" in dataframe.columns:
            slope_available  = dataframe["ema200_slope_4h"].notna()
            slope_trending_up   = (dataframe["ema200_slope_4h"] > 0) | ~slope_available
            slope_trending_down = (dataframe["ema200_slope_4h"] < 0) | ~slope_available
        else:
            slope_trending_up   = dataframe["close"] > 0   # always True
            slope_trending_down = dataframe["close"] > 0   # always True

        # Crossovers from the 1h informative pair (ema50_1h / ema200_1h).
        # Preferred over 15m EMAs: the 1h window (50h/200h) filters the noise that
        # makes 15m crossovers (12h/50h) whipsaws.  Falls back to 15m if 1h data
        # is unavailable (e.g. unit tests without a data provider).
        if "ema50_1h" in dataframe.columns and "ema200_1h" in dataframe.columns:
            fast_1h = dataframe["ema50_1h"]
            slow_1h = dataframe["ema200_1h"]
            crossed_up   = (fast_1h > slow_1h) & (fast_1h.shift(1) <= slow_1h.shift(1))
            crossed_down = (fast_1h < slow_1h) & (fast_1h.shift(1) >= slow_1h.shift(1))
        else:
            fast, slow = dataframe["ema_fast"], dataframe["ema_slow"]
            crossed_up   = (fast > slow) & (fast.shift(1) <= slow.shift(1))
            crossed_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))

        # ── Signal 1: trend long — Golden Cross ─────────────────────────────────
        # RSI ceiling (hyperopt-tuned): skip overbought entries — if RSI > rsi_long_max
        # the up-move is already extended and more likely to reverse.
        # 4h slope filter blocks longs when the macro trend is not rising.
        rsi_ok = dataframe["rsi"] <= self.rsi_long_max.value
        gc = crossed_up & volume_ok & rsi_ok & macro_up & slope_trending_up
        dataframe.loc[gc, "enter_long"] = 1
        dataframe.loc[gc, "enter_tag"]  = "ema_gc"

        # ── Signal 2: trend short — Death Cross ──────────────────────────────────
        # RSI floor (hyperopt-tuned): skip already-oversold candles — RSI < rsi_short_min
        # means momentum is already exhausted and a short here chases the move late.
        # 4h slope filter blocks shorts when the macro trend is not falling.
        rsi_not_oversold = dataframe["rsi"] >= self.rsi_short_min.value
        dc = crossed_down & volume_ok & rsi_not_oversold & macro_down & slope_trending_down
        dataframe.loc[dc, "enter_short"] = 1
        dataframe.loc[dc, "enter_tag"]   = "ema_dc"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict[str, Any]) -> DataFrame:
        """No signal-based exits — ROI, ATR stoploss, and custom_exit handle all closing.

        Historical analysis showed EMA-crossover exit signals produced 0 winning exits
        across 4 years of backtesting: 20 exits at 0% win rate, -$14 net P&L.  They
        fired on mean-reversion trades at the worst possible moment (mid-range) and
        triggered prematurely on trend trades before the ROI target was hit.

        Exits are managed by:
          - minimal_roi / roi table   → profit targets (hyperopt-tuned)
          - custom_stoploss (ATR)     → dynamic trailing stop + break-even lock
        """
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> str | None:
        """Reserved for future signal-specific early exits.

        Mean-reversion signals (rsi_bounce / rsi_drop) and their RSI-recovery exits
        were removed after consistently negative backtesting results.  All exits are
        now handled by minimal_roi (ROI table) and custom_stoploss (ATR trailing).
        """
        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float:
        """Always 1x — spot-equivalent exposure, no margin call risk above normal stoploss."""
        return 1.0

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> float:
        """ATR trailing stoploss with break-even protection — works for both longs and shorts.

        Two layers:
        1. ATR stop: trail in the profit direction — BELOW current price for longs,
           ABOVE current price for shorts (direction-aware).
        2. Break-even lock: once profit ≥ 2%, floor the stop at open_rate so a winning
           trade can never turn into a loss. stoploss_from_absolute handles the sign
           inversion for shorts automatically.

        The hard `stoploss = -0.10` class attribute is the absolute worst-case floor.
        """
        dp = self.dp
        if dp is None:
            return self.stoploss  # fail-safe: no data provider in tests
        dataframe, _ = dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty or "atr" not in dataframe.columns:
            return self.stoploss  # fail-safe: missing ATR

        atr = float(dataframe["atr"].iloc[-1])
        if atr <= 0:
            return self.stoploss  # fail-safe: bad ATR value

        # ATR multiplier — hyperopt-tuned via atr_trend_mult.
        # (atr_range_mult exists for potential range-signal re-enablement but is not
        #  currently used; breakout signals are also disabled.)
        enter_tag = getattr(trade, "enter_tag", None) or ""
        if enter_tag in ("breakout_long", "breakout_short"):
            atr_mult = self.BREAKOUT_ATR_MULT   # breakout disabled; keep default if re-enabled
        else:
            atr_mult = self.atr_trend_mult.value  # ema_gc / ema_dc / unknown

        # Layer 1: ATR trailing stop — direction-aware.
        # Long: stop BELOW current price (loss if price drops).
        # Short: stop ABOVE current price (loss if price rises).
        if trade.is_short:
            atr_stop_price = current_rate + atr * atr_mult
        else:
            atr_stop_price = current_rate - atr * atr_mult
        atr_stop = stoploss_from_absolute(atr_stop_price, current_rate, is_short=trade.is_short)

        # Layer 2: break-even lock — Hyperopt-tuned threshold (.value) replaces hardcoded 2%.
        # Once profit ≥ breakeven_pct, floor the stop at entry price.
        if current_profit >= self.breakeven_pct.value:
            be_stop = stoploss_from_absolute(trade.open_rate, current_rate, is_short=trade.is_short)
            # Return the higher (less negative) of the two stops — most protective wins.
            return max(atr_stop, be_stop)

        return atr_stop

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
            side=Side.SELL if side == "short" else Side.BUY,
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
        # Normally skipped in backtest/hyperopt — calling Bedrock per trade would
        # cost AWS credits, add latency, and pollute the audit trail with simulated
        # decisions.  The one-time validation switch (_advisor_in_backtest) opens this
        # path during backtest so the AI can be evaluated over historical signals.
        state = self._portfolio_state(current_time)
        opinion = None
        if self._is_live or self._advisor_in_backtest:
            ctx = self._build_market_context(pair, reference, current_time, analyzed, state)
            opinion = run_safe("advisor_opinion", lambda: self._ai().opinion(ctx), None)
        if opinion is not None:
            # Log every opinion (veto or not) so the AI's reasoning is fully auditable
            # — essential for the historical-replay validation, where we cross-check
            # each opinion against the actual trade outcome.
            logger.info(
                "ai_advisor_opinion: pair=%s side=%s bias=%.3f rationale=%s",
                pair,
                side,
                opinion.bias,
                opinion.rationale,
            )
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
            side=Side.SELL if side == "short" else Side.BUY,
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
