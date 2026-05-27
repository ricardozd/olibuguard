# Olibuguard — Overview

## What it is

Automated cryptocurrency trading bot on Binance.
Built as a personal learning project on top of **Freqtrade** with hexagonal architecture in Python.

Runs 24/7 in Docker. Supports two modes:
- **Paper mode** (`task paper-up`): dry-run, no real orders — ideal for live market validation.
- **Live mode** (`task docker-up`): real orders on Binance with minimal capital.

---

## Pairs and timeframe

| Parameter | Value |
|-----------|-------|
| Pairs | BTC/USDT, ETH/USDT |
| Timeframe | 5m (5-minute candles) |
| Exchange | Binance |
| Quote currency | USDT |

---

## Entry signal

The strategy uses an **EMA crossover** on the 5m candle:

1. **EMA 20** (fast) crosses **above** **EMA 50** (slow) → buy signal.
2. The strategy also computes:
   - **RSI 14** (Wilder EWM, no TA-Lib) — context for the AI advisor.
   - **Volume ratio** — current volume / 20-candle average — context for the AI advisor.
   - **ATR 14** (Wilder EWM, no TA-Lib) — used to compute the dynamic stoploss.

The exit signal is the reverse crossover (EMA 20 falls below EMA 50), complemented by `minimal_roi` (10%) and the dynamic stoploss.

---

## Position sizing

Size is calculated in two steps:

1. **Freqtrade** computes the stake based on `stake_amount = 50 USDT`.
2. **RiskGate** can reduce it (never increase):
   - Max 2% of real equity per trade.
   - Max 50 USDT absolute per position.
   - Max 200 USDT total open exposure.

---

## Decision flow per buy signal

```
EMA signal → confirm_trade_entry()
    │
    ├── Kill switch active? → REJECT
    │
    ├── AI Advisor (Bedrock / Claude Opus 4)
    │       Receives: price, EMAs, RSI, volume, drawdown, daily P&L
    │       → veto? → REJECT  (fail-safe: on any error, passes through)
    │
    └── RiskGate.evaluate()
            1. Circuit breakers active?            → REJECT
            2. Pair in blacklist?                  → REJECT
            3. Pair in whitelist? (if defined)     → REJECT if absent
            4. Orders-per-minute rate limit        → REJECT
            5. Excessive slippage?                 → REJECT
            6. Sizing: % equity + absolute caps
            7. Minimum notional (10 USDT)          → REJECT if below
            → APPROVE (possibly with reduced size)
```

---

## Stoploss

The bot uses a **double stoploss** for maximum protection:

| Layer | Mechanism | Detail |
|-------|-----------|--------|
| **Dynamic ATR** | Freqtrade `custom_stoploss` | `stop = entry_price − ATR×2`. Adapts to current volatility. |
| **Hard floor** | Class-level `stoploss = -0.10` | Freqtrade enforces this as ceiling: stoploss can never be worse than −10%. |
| **Exchange order** | `stoploss_on_exchange: true` | Freqtrade sends a stop order directly to Binance. If the bot goes down, Binance executes the stop. |

ATR is computed with Wilder's EWM formula (`ewm(com=13, adjust=False)`) — pandas-native, no TA-Lib.

---

## Circuit breakers (automatic halt)

The bot stops opening new positions automatically (no human needed) if:

| Condition | Threshold |
|-----------|-----------|
| Daily loss | ≥ 5% of equity |
| Drawdown from peak | ≥ 10% of peak equity |

Freqtrade also has its own **protections**:
- `CooldownPeriod`: 2-candle wait after closing a position.
- `MaxDrawdown`: halt if drawdown exceeds 10% over 48 candles.
- `StoplossGuard`: halt if ≥ 4 stop-losses fire within 24 candles.

---

## AI Advisor (AWS Bedrock / Claude Opus 4)

- Can only **veto** a trade — never initiate or enlarge one.
- Receives full context: price, EMAs, RSI, volume, last 5 closes, equity, drawdown, daily P&L.
- Uses **extended thinking** (5,000 reasoning tokens before deciding).
- If Bedrock fails or is unavailable → `NullAdvisor` → trade proceeds (fail-safe).

---

## Audit trail

Every decision is persisted in SQLite (`user_data/olibuguard_audit.sqlite`):

- **audit_log**: pair, price, risk gate verdict and reason.
- **equity_curve**: equity snapshot at most every 5 minutes.

On startup, the bot restores the **peak equity** from the DB so the drawdown circuit breaker
survives restarts without resetting.

---

## Telegram alerts

Automatic notifications for:
- Bot startup.
- Kill switch activation.
- Equity drift > 5% intra-candle (possible desync with the exchange).

---

## Manual kill switch

Stops new entries immediately without closing open positions:

```bash
task kill        # activate (creates KILL_SWITCH file)
task resume      # deactivate
```

The file `user_data/KILL_SWITCH` is the sentinel. The bot checks it before every entry.
