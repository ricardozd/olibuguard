# Olibuguard — Architecture

## Design principle

**Hexagonal architecture**: the olibuguard core (`olibuguard.*`) has no knowledge of Freqtrade.
All business logic lives in the core; Freqtrade is just an input/output adapter.

```
┌─────────────────────────────────────────────┐
│              Freqtrade (framework)           │
│                                             │
│   OlibuguardStrategy (adapter)              │
│   ├── populate_indicators   (signals+ATR)   │
│   ├── custom_stake_amount   (sizing)        │
│   ├── confirm_trade_entry   (decision)      │
│   └── custom_stoploss       (dynamic ATR)   │
│                    │                        │
└────────────────────┼────────────────────────┘
                     │ domain types
                     ▼
┌─────────────────────────────────────────────┐
│              olibuguard core                │
│                                             │
│   RiskGate          risk/gate.py            │
│   AIAdvisor         advisor/bedrock.py      │
│   KillSwitch        kill_switch.py          │
│   AuditSink         audit/sqlite.py         │
│   AlertSink         alerts/telegram.py      │
│   Reconciliation    reconciliation.py       │
└─────────────────────────────────────────────┘
```

---

## Core modules

### `domain/`
Pure types with no external dependencies.
- `models.py` — `OrderIntent`, `PortfolioState`, `RiskVerdict`, `MarketContext`, `Side`
- `ports.py` — Protocols (interfaces) for `AIAdvisor`, `AuditSink`, `AlertSink`

### `risk/gate.py` — RiskGate
The invariant module. Evaluates an `OrderIntent` against the `PortfolioState` and returns a `RiskVerdict`.
Can reject or reduce size — never increase. See [overview.md](overview.md) for evaluation order.

### `advisor/`
- `base.py` — `AIAdvisor` Protocol + `NullAdvisor` (no-op, fail-safe default)
- `bedrock.py` — `BedrockAdvisor`: calls Claude via AWS Bedrock. Any error → `None` (abstention).

### `audit/`
- `records.py` — `DecisionAudit`, `EquityPoint` (dataclasses)
- `sink.py` — `AuditSink` Protocol + `NullAuditSink`
- `sqlite.py` — `SQLiteAuditSink`: real persistence
- `version.py` — hash of the running code for the audit trail

### `alerts/`
- `sink.py` — `AlertSink` Protocol + `NullAlertSink`
- `telegram.py` — `TelegramAlertSink`: sends messages to the Telegram bot

### `kill_switch.py`
File sentinel on disk. `is_active()` = file exists.
`task kill` / `task resume` create/delete it.

### `reconciliation.py`
- `restore_peak_equity()` — reads peak equity from DB on startup
- `check_equity_drift()` — detects > 5% desync between DB and exchange

### `failsafe.py`
- `run_safe(label, fn, default)` — runs `fn`, catches any exception, returns `default`
- `ErrorBudget` — consecutive failure counter; after N failures activates the kill switch

### `config.py`
Pydantic models for `AppConfig`, `RiskLimits`, `AIConfig`. Strict validation (`extra = "forbid"`).

---

## Data flow per candle

```
New 5m candle
    │
    ▼
populate_indicators()
    Computes EMA 20, EMA 50, RSI 14, volume_ratio, ATR 14
    Generates signal: ema_cross_up = 1 if EMA20 > EMA50
    │
    ▼
populate_entry_trend()
    enter_long = ema_cross_up
    │
    ▼  (if signal present)
custom_stake_amount()
    RiskGate caps the maximum allowed size
    │
    ▼
confirm_trade_entry()
    1. Kill switch active? → False
    2. AI Advisor → veto? → False
    3. RiskGate.evaluate() → approved? → True/False
    4. Audit: record the decision

In parallel, for each open position on every new candle:
    ▼
custom_stoploss()
    Reads ATR from the last analyzed candle
    stop_price = current_rate − ATR × 2
    Returns relative stoploss (never worse than −10%)
    → Freqtrade syncs the stop order on Binance (stoploss_on_exchange)
```

---

## Security principles

1. **Fail-safe**: any optional component (AI, audit, alerts) that fails → bot continues.
2. **Veto-only AI**: the AI can only reject, never initiate or enlarge.
3. **Double circuit breaker**: olibuguard (code) + Freqtrade (protections) as a second net.
4. **Double stoploss**: dynamic ATR via code (`custom_stoploss`) + stop order on Binance (`stoploss_on_exchange`). If the bot goes down, Binance still executes the stop.
5. **No keys on disk**: exchange credentials via env vars; AWS credentials via temporary STS tokens.
6. **Immutable audit**: SQLite append-only — records are never overwritten.
7. **Instant kill switch**: file on disk, checked before every entry.
