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
| **4 – Live** | Futures long+short, investor capital ($500), backtest +2.14% | 🟡 in progress |

---

## Pending phases

---

### Phase 4 — Live validation (now)

**Estimated duration**: 4–8 weeks

**What to do**:
- [x] First positive backtest: +1.01%, 59 trades, 4.58% max drawdown (Oct 2024–Apr 2025)
- [x] Paper bot running 24/7 in Docker (auto-start after rebuild)
- [x] Safety layers validated: circuit breakers, audit DB isolation, kill-switch
- [x] Break-even stop implemented: prevents +3% winners from becoming -2% losers
- [x] Strategy stable: Golden Cross EMA 50/200 on 15m, 4 pairs (BTC/ETH/SOL/BNB)
- [x] **Futures long+short**: Death Cross = short, 1x leverage, bidirectional. Backtest +2.14%, 121 trades, DD 1.24%
- [x] Investor capital: account raised to $500, risk limits tightened (daily loss 3%, drawdown 7%)
- [ ] **See first live trade** (bot in paper mode on futures, waiting for Golden Cross or Death Cross signal)
- [ ] Run paper for 5+ days once first trade fires — verify stake, entry, exit are correct
- [ ] Fund Binance futures with $500 USDT and switch to `task docker-up` (real orders)
- [ ] Run real orders for 30 days — verify circuit breakers and AI veto work live
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
- [x] Timeframe 5m → 15m (less noise, better signal quality)
- [x] EMA 20/50 → Golden Cross EMA 50/200 (higher quality signals)
- [x] RSI ≤ 65 entry filter (avoid overbought entries)
- [x] EMA200 1h macro trend filter (no trades against the macro trend)
- [x] Break-even stop @+2% (win never becomes loss)
- [x] Pairs: BTC + ETH + SOL + BNB (4 pairs, more signal frequency)
- [ ] **Hyperopt**: optimize EMA periods, ATR multiplier, ROI — currently 0.14%/month, need 8%
- [ ] Backtest over 12+ months (currently only 7 months)
- [ ] Volume confirmation: revisit with better threshold (≥1.0 killed 71% of trades)
- [ ] RSI dip buy: tested and removed — needs multi-timeframe support to avoid false signals
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
- [x] Long-term trend filter (price above EMA 200 on 1h) — already implemented
- [ ] ADX filter: if ADX < 20 (flat market) → skip EMA crossover entries
- [ ] Mean-reversion strategy for ranging periods (RSI dip buy needs multi-TF support first)

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
| Backtest 7m (spot) | $100 sim | +$1.01 | +1.01% | 4.58% | Oct 2024–Apr 2025; BTC+ETH+SOL+BNB longs-only |
| Backtest 7m (futures) | $500 sim | +$10.69 | +2.14% | 1.24% | Oct 2024–Apr 2025; ETH+SOL+BNB longs+shorts (57L/64S); **BTC excluded by exchange min-stake**; shorts avg +1.61% |
| 2026-06 | $500 paper | — | — | — | Phase 4: paper bot live on futures, waiting for first trade (market in Death Cross) |

---

## Decision log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-05 | ATR dynamic stoploss + stoploss_on_exchange | Adapts to volatility; survives bot downtime |
| 2026-05 | Timeframe 1h → 5m | More signal frequency for compounding |
| 2026-05 | AI Advisor (Claude Opus 4 + extended thinking) | Veto-only safety layer |
| 2026-05 | Dual paper/live mode via `FREQTRADE__DRY_RUN` | Same image, two modes |
| 2026-05 | EMA 20/50 → Golden Cross EMA 50/200 on 15m | Fewer but higher-quality signals; win rate ↑ from ~15% to 32-39% |
| 2026-05 | Drop BTC/USDT, trade ETH/SOL/BNB | Altcoins trend more sharply → better Golden Cross quality; BTC was -1.42% drag |
| 2026-05 | Break-even stop @+2% profit | Prevents +3% winners from reverting to -2% losses; tipped backtest positive |
| 2026-05 | ROI target 7% (from sweep 5%/7%/10%) | Local optimum; best trade hits 7% in backtest confirming target is reachable |
| 2026-05 | Add BTC back (4 pairs total) | Break-even stop neutralises BTC drag; more pairs = more signal frequency |
| 2026-05 | RSI dip buy tested and removed | Catching falling knives even with Golden Cross + 1h EMA200 filter; removed |
| 2026-05 | Audit DB isolated from backtests | `_is_live` gate prevents simulated decisions polluting live audit trail |
| 2026-05 | Futures long+short (1x leverage, isolated margin) | Operates in bull AND bear markets; Death Cross → short; shorts avg +1.61% in backtest |
| 2026-05 | Investor capital $500, tighter risk limits | daily_loss 3%, drawdown 7%; stake $50/trade (10% of account) |
| 2026-05 | AppConfig default whitelist = [] (no filter) | Fail-safe: when no config loaded, all pairs allowed; real filter lives in config.yaml |
