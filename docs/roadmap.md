# Olibuguard — Roadmap

**End goal**: generate **$300–500/month** for the family.
**Maximum investment**: $100 starting capital — no additional deposits.
**Path**: prove a real edge, then let compounding do the work.

---

## The math: $100 → $300–500/month via compounding

To reach $300–500/month from a $100 starting account, pure compounding must grow
the account to ~$5,000–$10,000. How long that takes depends entirely on monthly return:

| Monthly return | Account after 3 years | Account after 5 years | Monthly income at that point |
|---|---|---|---|
| 3 %/month | $295 | $744 | $22/month |
| 5 %/month | $576 | $1,847 | $92/month |
| 8 %/month | $1,513 | $5,862 | **$469/month** ✅ ~5 years |
| 10 %/month | $3,091 | $11,739 | **$1,174/month** ✅ ~3.5 years |

**Key insight**: 8 %/month consistently over 5 years gets there. 10 %/month in ~3.5 years.
These are ambitious targets — they require a genuinely profitable strategy, not just a working bot.

**Rules**:
- Never withdraw — every cent of profit stays in the account.
- Never deposit more — the $100 is the total investment.
- Capital preservation is #1: losing the $100 means starting over from zero.

---

## Completed phases

| Phase | Description | Status |
|-------|-------------|--------|
| **0 – Setup** | Hexagonal skeleton, tooling, smoke tests | ✅ |
| **1 – Strategy** | EMA 20/50 on 5m, reproducible backtest | ✅ |
| **2 – Safety layer** | Circuit breakers, audit DB, kill-switch, error budget, Telegram | ✅ |
| **3 – AI Advisor** | Claude Opus 4 via Bedrock, veto-only, extended thinking | ✅ |
| **4 – Live** | Real Binance orders, ATR stoploss, stoploss_on_exchange | 🟡 in progress |

---

## Pending phases

---

### Phase 4 — Live validation (now)

**Estimated duration**: 4–8 weeks

**What to do**:
- [ ] Fund Binance with real minimum capital ($100 USDT)
- [ ] Run `task docker-up` with real orders for 30 days
- [ ] Verify safety layers work in production (circuit breakers, Binance stop orders)
- [ ] Confirm AI advisor is adding value — log its veto rate and reasons
- [ ] Compare real P&L vs paper: large divergence = slippage or execution issue

**Success criterion**: bot alive for 30 days, drawdown < 10%, no catastrophic failures.
Profit amount doesn't matter yet — the goal is proving the system is stable.

---

### Phase 5 — Measure and improve the strategy

**Estimated duration**: 4–8 weeks after Phase 4

**Target metrics**:

| Metric | Minimum | Target |
|--------|---------|--------|
| Win rate | > 45% | > 55% |
| Profit factor | > 1.2 | > 1.5 |
| Sharpe ratio | > 0.8 | > 1.2 |
| Max drawdown (backtest) | < 15% | < 10% |
| Trades/month | > 20 | 40–80 |
| **Monthly return** | > 3% | **> 8%** |

**What to do**:
- [ ] Backtest with 12+ months of 5m data (BTC/USDT + ETH/USDT)
- [ ] Hyperopt: optimize EMA periods, ATR multiplier, minimum ROI
- [ ] Add RSI entry filter: avoid buys when RSI > 70 (overbought)
- [ ] Add volume confirmation: skip signal if volume < 20-candle average
- [ ] Evaluate adding SOL/USDT and BNB/USDT for more signal frequency
- [ ] Only merge changes that improve backtest metrics — never "improve" blindly

**Success criterion**: Sharpe > 1.0 in backtest, profit factor > 1.4, monthly return > 5%.

---

### Phase 6 — Compound and track

**Estimated duration**: ongoing from Phase 5

**Compounding rules**:
- All profits stay in the account — zero withdrawals
- Raise Freqtrade's `stake_amount` in `config.json` as the account grows
- Raise `max_position_quote` and `max_total_exposure_quote` in `config.yaml` proportionally
- Never increase a config limit without 30 consecutive green days at the current level

**Account growth milestones**:

| Milestone | Account size | Projected monthly income | Action |
|-----------|-------------|--------------------------|--------|
| Start | $100 | < $10 | Validate live trading |
| 🟡 Traction | $500 | ~$25–40 | Confirm edge is real and consistent |
| 🟠 Building | $2,000 | ~$100–160 | Expand pairs if not already done |
| 🔴 Scaling | $5,000 | ~$250–400 | Strategy is proven — protect capital |
| ✅ **Goal** | $8,000–$10,000 | **$300–500** | Sustained for 3+ months |

**What to do**:
- [ ] Update `stake_amount` every time the account doubles
- [ ] Update risk limits (`config.yaml`) proportionally
- [ ] Log monthly P&L in the table at the bottom of this file

---

### Phase 7 — Market regime resilience

**Estimated duration**: parallel to Phase 6

**Current problem**: EMA crossover only works in trending markets. In ranging markets it generates
false signals and loses money. This directly limits monthly return.

**What to do**:
- [ ] Add market regime filter: compute ADX or Bollinger Band Width
  - If ADX < 20 (flat market) → skip EMA crossover entries
- [ ] Or simply: add a long-term trend filter (e.g. price above EMA 200 on 1h) to avoid
  trading against the macro trend
- [ ] Evaluate a second mean-reversion strategy for ranging periods to fill the gap

**Success criterion**: fewer false signals in backtests, better Sharpe in ranging periods.

---

### Phase 8 — Goal achieved

**Criterion**: 3 consecutive months with $300–500 net real profit.

**What to maintain**:
- [ ] Renew AWS STS tokens every 12h (or automate with a cron job)
- [ ] Review backtest quarterly — the market changes, strategies drift
- [ ] Active monitoring of circuit breakers and audit log
- [ ] Keep a cash reserve: never put more than 30% of total savings into the bot

---

## Monthly P&L log

| Month | Account | P&L $ | P&L % | Max drawdown | Notes |
|-------|---------|--------|--------|--------------|-------|
| 2026-06 | $100 paper | — | — | — | live validation in progress |

---

## Decision log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-05 | ATR dynamic stoploss + stoploss_on_exchange | Adapts to volatility; survives bot downtime |
| 2026-05 | Timeframe 1h → 5m | More signal frequency for compounding |
| 2026-05 | AI Advisor (Claude Opus 4 + extended thinking) | Veto-only safety layer |
| 2026-05 | Dual paper/live mode via `FREQTRADE__DRY_RUN` | Same image, two modes |
