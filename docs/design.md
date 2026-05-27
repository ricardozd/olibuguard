# Crypto Trading Bot — Design and Decisions

Living document. Captures the vision, technical decisions, and guardrails applied to the project.
Updated as the bot evolves.

Date: 2026-05-26 (last updated 2026-05-27)
Author: Oliv (with Claude assistance)

---

## 1. Vision and Constraints

Build a cryptocurrency trading bot to run locally on Windows and macOS, as a personal learning and
experimentation project. Phase 1's goal is not to make money but to produce an auditable, robust,
and safe piece of software capable of operating in simulation mode (paper trading) for an extended
validation period before any real capital is risked.

Constraints that shape every design decision:

- **Security over development speed.** Any trade-off is resolved in favour of robustness,
  traceability, and fail-safe behaviour.
- **Paper trading first.** No code runs in `live` mode until the bot has weeks or months of
  failure-free operation in simulation.
- **Guardrails from day one.** Risk limits are not a future improvement — they are part of the
  minimum viable architecture.
- **Pluggable AI.** Any AWS Bedrock integration (or other LLM) must be disableable with a flag
  without losing any core functionality.
- **Local on Windows and macOS.** No cloud dependencies in the main decision cycle. Local disk
  persistence.

---

## 2. Language: Python vs Go

The recommendation is **Python with strict typing**, not Go.

### Why Python wins here

The crypto/finance ecosystem in Python is far ahead of Go. In 2026 the key pieces remain:

- **`ccxt` 4.5.x** — reference library for 100+ exchanges (Binance, Kraken, Coinbase). Actively
  maintained.
- **`Freqtrade` 2026.4** — FOSS framework for crypto bots. Natively supports paper trading
  (dry-run), backtesting, and hyperparameter optimisation. Closest thing to what we want to build.
- **`vectorbt` 1.0** — modern vectorised backtesting. (`backtrader` abandoned since 2023 — do not
  use.)
- **`pandas`, `numpy`, `TA-Lib`** — mature technical analysis.
- **`pydantic`** — runtime validation for configs and messages.
- **`boto3`** — official AWS SDK for Bedrock integration when/if desired.

The argument "Go is compiled and therefore safer" does not hold once you apply in Python:

- `mypy --strict` or `pyright strict` in local CI.
- `pydantic` for runtime validation of configs and events.
- Full domain typing (no `Any`).
- `pytest` with broad coverage, especially the risk module.

Python then provides guarantees very close to a compiled language for this kind of application at
far lower cost.

### When I would switch to Go

Only if there is ever a real need for very low latency (scalping, competitive market making). For
swing/day trading on minute or hour candles, Python latency is irrelevant. The decision is
revisable, but the initial bet is Python.

### Recommended concrete stack

```
Python 3.12+
- ccxt                         (exchange access)
- pandas / numpy               (data)
- pydantic v2                  (validation)
- SQLAlchemy 2 + SQLite        (persistence)
- structlog                    (structured JSON logging)
- rich                         (nice CLI)
- pytest + hypothesis          (tests)
- mypy --strict                (local CI typing)
- ruff                         (lint and format)
```

Project tools: `uv` for dependencies (fast, reproducible lock file). `go-task` as task runner.
Pre-commit hook with ruff + mypy.

---

## 3. Build from scratch or start with Freqtrade?

Two honest paths:

### Path A — Build on Freqtrade (recommended)

Freqtrade already solves, proven in production by thousands of users:

- Exchange connectivity (via ccxt).
- Dry-run / paper trading natively.
- Backtesting and hyperopt.
- Custom Python strategy system.
- Persistence (SQLite by default).
- REST API + Telegram for control and remote kill-switch.
- Stop-loss, trailing stop, take-profit, position sizing.

On that foundation, what olibuguard adds:

- Own **risk gate** layer wrapping all orders (second safety net on top of Freqtrade's).
- Custom **strategies**.
- Optional **AI advisor** module (Bedrock) behind an interface.
- Own **alert and audit** system.

Advantage: start with battle-tested guardrails, don't reinvent them. Focus on what adds unique
value (strategy, optional AI, monitoring).

### Path B — Build from scratch with ccxt

Absolute control, deeper learning, but also more code, more bugs, and far more time before having
something safe. Reinventing order management, persistence, backtest, dry-run, etc.

### Recommendation

**Start with Path A (Freqtrade) and keep the exit option open.** The strategy and risk gate layers
are written so they are not coupled to Freqtrade — migrating to a custom stack later remains
possible. But starting by wrestling with exchange plumbing is a distraction from the learning goal,
which is trading logic and guardrails.

### Decision (2026-05-26): Path A — Freqtrade

Confirmed. Notes from the pre-verification:

- **Version and Python**: Freqtrade `2026.4` supports Python 3.11–3.14, so the current venv
  (3.12) works. On macOS ARM64 Freqtrade recommends Docker; native installation is possible but
  not officially supported. Native Windows uses VC++ Build Tools. TA-Lib remains a system
  dependency.

**How we integrate without coupling**: an `OlibuguardStrategy(IStrategy)` class acts as a thin
adapter and delegates to the `olibuguard.*` core (which does not import Freqtrade). Hook map:

| Freqtrade hook (`IStrategy`) | What olibuguard provides |
|---|---|
| `populate_indicators` / `populate_entry_trend` / `populate_exit_trend` | Signal generation (our `StrategyPort`). Vectorised; called once in backtest. |
| `custom_stake_amount(...) -> float` | Sizing: risk gate applies `max_position_quote`, available exposure, and the advisor's reduce-only factor. |
| `confirm_trade_entry(...) -> bool` | Final gate: risk gate can **veto** (`return False`). Second safety net on top of Freqtrade's. |
| `bot_loop_start(...)` | Equity curve snapshot + equity drift reconciliation check. |

The adapter is the boundary: Freqtrade works in `float`; our core in `Decimal`. Note: in backtest,
callbacks behave differently (simulated `wallets` state) — validated there too.

**Native installation:**

- **macOS (ARM64)**: `brew install ta-lib`, then `uv sync --extra freqtrade` after exporting
  `TA_INCLUDE_PATH=/opt/homebrew/opt/ta-lib/include`, `TA_LIBRARY_PATH=…/lib`, and
  `PKG_CONFIG_PATH=…/lib/pkgconfig` to compile the `ta-lib` wrapper.
- **Windows**: install VC++ Build Tools ("Desktop development with C++") before
  `uv sync --extra freqtrade`.
- The venv is managed by `uv` with Python 3.12 (meets `requires-python`, most battle-tested with
  Freqtrade).

**Smoke backtest** (Phase 1): `task download -- --timerange <range>` then `task backtest -- --timerange <range>`. The EMA20/50 strategy is a skeleton — the goal is validating infrastructure, not returns.

---

## 4. Architecture

Hexagonal style (ports and adapters) so the decision core is testable in isolation and external
components (exchange, AI, persistence) are replaceable.

```
┌─────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                           │
│  CLI / TUI / local API — start, stop, kill-switch, status       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                       ORCHESTRATOR (loop)                        │
│  tick → fetch market → strategy → risk gate → order manager     │
└──┬────────────┬─────────────┬────────────┬────────────┬─────────┘
   │            │             │            │            │
┌──▼───┐   ┌────▼────┐   ┌────▼─────┐ ┌────▼─────┐ ┌───▼──────┐
│Market│   │Strategy │   │Risk Gate │ │Order Mgr │ │AI Advisor│
│ Data │   │ Engine  │   │(guards)  │ │          │ │(optional)│
└──┬───┘   └─────────┘   └──────────┘ └────┬─────┘ └──────────┘
   │                                       │
┌──▼─────────┐                       ┌─────▼──────┐
│Exchange    │                       │Exchange    │
│Adapter     │                       │Adapter     │
│(ccxt)      │                       │(ccxt)      │
└────────────┘                       └────────────┘

┌────────────────────────────────────────────────────────────────┐
│  PERSISTENCE (local SQLite)                                    │
│  trades, orders, decisions, audit_log, market_snapshots        │
└────────────────────────────────────────────────────────────────┘
```

Components:

- **Market Data**: reads prices, candles, order book via ccxt (WebSockets when possible, REST
  fallback).
- **Strategy Engine**: produces signals (BUY/SELL/HOLD) from data. **Does not know** the broker,
  account size, or limits. Pure data → intent function.
- **Risk Gate**: the most important module. Receives the strategy's intent and decides whether to
  execute and at what size. Can reject any order. See section 5.
- **Order Manager**: translates approved intents into real orders, manages lifecycle
  (open → filled / cancelled / partial), reconciles with exchange state.
- **AI Advisor (optional)**: see section 7.
- **Persistence**: see section 6.
- **Control Plane**: minimal CLI initially; eventually a TUI with `Textual` or local web monitor.
  Allows viewing state, stopping everything, activating the kill switch.

Principle: **the strategy does not trade, it proposes**. What trades is the combination of risk
gate + order manager. Strategy is replaceable; the risk gate is invariant.

---

## 5. Guardrails (most important)

Complete catalogue of protections that must be in place before the bot touches real money. Many are
inherited from Freqtrade but all are verified and, where applicable, duplicated in the olibuguard
risk gate.

**Implementation status (2026-05-27)** — synchronised with the code.
Legend: ✅ done · 🔶 partial · ⏳ pending

| Guardrail | Status | Where |
|---|---|---|
| 5.1 Modes + live confirmation | ✅ | `olibuguard/modes.py`, `olibuguard/cli.py` |
| 5.2 Hard limits + %-capital sizing | ✅ | `olibuguard/risk/gate.py`, `olibuguard/config.py` |
| 5.3 Circuit breakers (daily loss, drawdown, order rate) | ✅ own gate + Freqtrade protections (MaxDrawdown, StoplossGuard, CooldownPeriod) | `risk/gate.py`, `OlibuguardStrategy.py` |
| 5.4 Kill switch | ✅ file sentinel (`KILL_SWITCH`) + `task kill/resume` + auto-activation via error budget | `kill_switch.py`, `cli.py`, `failsafe.py` |
| 5.5 Idempotence and reconciliation | ✅ peak equity restored from audit DB at startup; equity drift check per candle | `reconciliation.py`, `audit/` |
| 5.6 Credentials | ✅ `keyring` + `python-dotenv` (`.env`), never in code | `olibuguard/secrets.py`, `.env.example` |
| 5.7 Audit log | ✅ `SQLiteAuditSink`: every risk-gate decision + equity curve in a separate DB | `olibuguard/audit/` |
| 5.8 Sanity checks (smoke + property-based) | ✅ smoke test + hypothesis; backtest CI ⏳ | `tests/`, `olibuguard/cli.py` |

Details per guardrail below. Dynamic sizing (`max_risk_per_trade_pct`, % of equity) and circuit
breakers (`daily_loss_limit_pct`, `max_drawdown_pct`) live in the risk gate; the Freqtrade adapter
applies them in `custom_stake_amount` / `confirm_trade_entry`.

### 5.1 Bot modes (hard separation)

Three mutually exclusive modes, configured at process level, not at runtime:

1. **`backtest`** — runs over historical data, no exchange connection.
2. **`paper` (dry-run)** — connected to the exchange in read-only mode; orders are simulated in
   memory/SQLite and never sent.
3. **`live`** — real orders.

Mode is decided at process startup via environment variable or CLI argument and is fixed.
Changing mode requires a restart. The binary cannot switch from `paper` to `live` by itself.

`live` mode requires the explicit confirmation flag `--i-understand-this-is-real-money`, and the
bot logs the mode in bright colours at startup.

### 5.2 Hard limits — enforced before every order

All checked by the risk gate, all configurable, all with conservative defaults:

- **Max position size per trade** (in quote currency or as % of capital, whichever is smaller).
- **Max total exposure** (sum of open positions).
- **Max number of open positions** simultaneously.
- **Max orders per minute** (anti-runaway).
- **Min order size** (avoids orders where fees > P&L).
- **Max tolerated slippage** between signal price and execution price.
- **Pair whitelist**: the bot can only trade pairs in the list, even if the strategy requests
  others.
- **Pair blacklist**: explicitly prohibited pairs (rare stablecoins, newly listed tokens, etc.).

### 5.3 Circuit breakers — during operation

Conditions that automatically pause the bot:

- **Daily loss limit**: if daily realised P&L drops below −X% of equity → STOP, no new positions
  until manual intervention.
- **Drawdown from peak**: if equity falls more than Y% from the peak → STOP.
- **Order rate anomaly**: if M orders are executed in N minutes (anomaly vs. historical average) →
  STOP.
- **Price sanity check**: if the pair's price changes > X% in N seconds → skip tick and/or pause
  the pair.
- **Exchange disconnect**: if the exchange connection is lost for more than N seconds → cancel
  pending orders and enter "no new trades" mode until reconnection.
- **Clock skew**: if the difference between the local clock and the exchange's clock exceeds a
  threshold → STOP (typical symptom of signed-request failures).

### 5.4 Kill switch

Three independent ways to stop the bot, in order of severity:

1. **Soft stop**: stop opening new positions; keep existing ones until their exit rules fire.
2. **Flat-and-stop**: cancel pending orders, close open positions at market, halt.
3. **Hard kill**: terminate the process. Next startup performs reconciliation.

Kill-switch triggers:
- **File sentinel**: if `user_data/KILL_SWITCH` exists, all new entry decisions are vetoed.
  Create with `task kill`; remove with `task resume`.
- **Error budget exhaustion**: `ErrorBudget` auto-activates the kill switch after 5 consecutive
  wallet-read failures (flying blind on equity is unsafe).
- **CLI command**: `olibuguard kill [--reason "…"]`.
- Freqtrade's `MaxDrawdown` / `StoplossGuard` protections act as a second runtime net.

### 5.5 Idempotence and reconciliation

- Each order carries a deterministic unique `client_order_id` generated and stored in SQLite
  *before* being sent to the exchange. If the bot crashes between "decided to send" and
  "exchange confirmed", on startup it can query that ID against the exchange and determine whether
  the order arrived.
- **Peak equity restored from audit DB at startup**: `_peak_equity` is not reset to zero on restart.
  The drawdown circuit breaker therefore reflects the true historical peak, not just the
  post-restart equity.
- **Equity drift check**: on every `bot_loop_start` tick, the adapter compares current wallet
  equity against the last recorded audit snapshot. A drift > 5% triggers a warning (and a Telegram
  alert if configured). This detects external account activity the bot is unaware of.

### 5.6 Credentials

- Exchange API keys **must not** be stored in plain files on disk. Use the Windows credential
  manager (DPAPI via `keyring`) or a `.env` file. *(Implemented: `olibuguard/secrets.py` uses
  `keyring`; `.env` loaded via `python-dotenv`; template in `.env.example`. For Freqtrade in
  live mode, keys are injected as `FREQTRADE__EXCHANGE__KEY/SECRET`.)*
- API keys must be created with **minimum permissions**: trading yes, withdrawal **no**. Enable IP
  whitelist on the exchange.
- Separate key for paper vs live environments when the exchange supports it.
- Never log keys, even masked, even at debug level.

### 5.7 Audit log

Every risk-gate decision is persisted in an `audit_log` table (separate SQLite DB from
Freqtrade's) with:

- Timestamp (timezone-aware, stored as UTC ISO-8601).
- Input snapshot: symbol, reference price, equity at the time.
- Risk-gate verdict: approved / reduced / rejected + reason.
- Approved quote amount (if any).
- Git commit SHA of the bot code that made the decision.

An `equity_curve` table records periodic equity snapshots for coherence checks against backtests.

This allows reproducing any decision post-mortem and is the foundation for improving the strategy
with real operational data.

### 5.8 Sanity checks during development

- Mandatory unit tests for the risk module. Property-based testing with `hypothesis` for hard
  limits.
- Integration test: backtest over one year of historical data as part of local CI.
- Smoke test before every startup: bot reads config, verifies that the risk gate rejects a couple
  of invalid orders (internal self-check), then starts operating.

### 5.9 Execution reality (market friction)

Tips to ensure what is profitable in backtest survives in live. Legend: ✅ done · 🔶 partial ·
⏳ Phase 2 · 🔌 covered by Freqtrade.

- **Commissions (maker/taker)**: limit orders (maker) by default + explicit `fee` in `config.json`. ✅
- **Spread (bid/ask)**: Freqtrade quotes against the order book (`use_order_book`), not the
  `close`. ✅
- **Slippage**: guard in the risk gate (`max_slippage_pct`). ✅
- **Clock sync**: `adjustForTimeDifference` enabled in `ccxt_config`. ✅ · NTP on the OS is a
  deployment responsibility. 🔶
- **WebSockets / rate limits**: managed by Freqtrade. 🔌
- **Reconnection without duplicate orders**: Freqtrade reconnects and persists; own reconciliation
  at startup → Phase 2. ✅
- **Liquid pairs**: whitelist BTC/USDT, ETH/USDT. ✅
- **Closed candles (no look-ahead)**: `process_only_new_candles` + crossover with `shift(1)`. ✅
- **Extended paper trading (≥ 4 weeks)**: Phase 2 exit criterion. ⏳ next milestone

---

## 6. Persistence

**Local SQLite** as the main store. Sufficient and appropriate for personal volume — no external
service, backups = one file.

Minimum tables:

- `config_snapshot` — full serialised config on every startup, for traceability of parameters.
- `market_data` — downloaded candles / ticks (optional — can be re-fetched from the exchange).
- `signals` — signals produced by the strategy, approved and rejected.
- `orders` — submitted orders with state.
- `trades` — completed operations (open + close pair).
- `audit_log` — see 5.7.
- `equity_curve` — account value snapshot every N minutes.

Backups: `litestream` or a simple copy of the `.sqlite` file every N hours to a second location
(another drive, OneDrive). The file stays small.

Schema managed with `alembic` (migrations), even for a solo project: guarantees future schema
changes do not break historical data.

---

## 7. Optional AI (Bedrock) — clean interface

The requirement is that the AI is pluggable. The pattern:

### Abstract interface

```python
class AIAdvisor(Protocol):
    def opinion(self, context: MarketContext) -> AdvisorOpinion | None:
        """Return None if the advisor has no opinion or is disabled."""
```

`AdvisorOpinion` is never an order; it is a score / bias between −1 and +1 with a justification
text. The strategy decides how much weight to give it (can be 0). The risk gate is never bypassed
because the AI "is very confident".

### Implementations

- `NullAdvisor` — always returns `None`. **This is the default.** The bot works perfectly without AI.
- `BedrockAdvisor` — uses boto3 → bedrock-runtime. Model-configurable. Only loaded if enabled in
  config.

### Enabling

In `config.yaml`:

```yaml
ai:
  enabled: false        # default false
  provider: bedrock     # or "null"
  model: anthropic.claude-...
  weight: 0.0           # how much weight the strategy gives it; 0 = ignored
  region: eu-west-1
```

If `enabled: false` or `provider: null`, the Bedrock code is not even imported. The `boto3`
dependency is optional (declared as an extra in `pyproject.toml`).

### Safeguard

The AI can only **veto or reduce** a trade, never **initiate or enlarge** it beyond what the
strategy proposed. This is an explicit guardrail: even if a future LLM says "buy everything you
have", the risk gate and the strategy cap ignore it.

---

## 8. Strategy for starting out

Do not start by trying to predict price. That is the trap where 90% of personal bots fall, and
where P&L variance is so large that you cannot tell whether your bot is good or just lucky.

Start with a known, simple, auditable, and well-studied strategy that serves as the system
skeleton. Three reasonable candidates for Phase 1:

1. **Mean reversion with Bollinger Bands + RSI** on liquid pairs (BTC/USDT, ETH/USDT). Buy when
   price < lower band and RSI < 30; sell when it returns to the mean or RSI > 70. Intuitive
   behaviour, easy to reason about.

2. **Moving average crossover (EMA 20 / EMA 50)** on the 1h or 4h timeframe. Trend-following
   behaviour. Long periods with no trades — which is good for validating that the bot is sane.

3. **DCA + dynamic take-profit** (Dollar Cost Averaging). The bot buys small amounts on predefined
   dips and sells when a target margin is reached. The least "trading" of the three, the most
   "programmatic saving".

Suggestion: start with (2) or (3). Boring is exactly what we want in Phase 1. The goal is not
to maximise return but to validate that the infrastructure works reliably for months.

Once the infrastructure is stable, strategy becomes a parameter: test variants in backtest,
hyperopt, and only promote to paper trading those that pass statistical filters (Sharpe, max
drawdown, sufficient trade count to not be noise).

---

## 9. Roadmap by phase

Strict order. Do not advance to the next phase until the exit criteria are met.

### Phase 0 — Setup ✅
- Git repo, Python 3.12, uv, ruff, mypy, pytest, pre-commit.
- Empty hexagonal folder structure with defined interfaces.
- Pydantic config, structured logging, secrets via keyring.
- Smoke test that starts the process, reads config, and exits cleanly.

### Phase 1 — Backtest end-to-end ✅
- Market data adapter reading historical data (via Freqtrade / ccxt `fetch_ohlcv`).
- A trivial strategy (EMA crossover) implemented against the interface.
- Backtest engine (Freqtrade) running and producing equity curve.
- Unit tests for the risk module with hypothesis property-based testing.
- **Exit criterion met**: reproducible backtest runs and produces a P&L chart.

### Phase 2 — Safety layer ✅
Full safety stack built and verified (76 tests, mypy --strict, ruff clean):

- **A. Circuit-breaker state wiring** — adapter feeds `PortfolioState` with real equity (via
  `wallets`), in-memory peak equity (restored from audit DB on restart — E), and today's realised
  PnL (`Trade.get_trades_proxy`). Drawdown and daily-loss circuit breakers active as a second net.
  All reads defensive (fail-safe).
- **B. Audit DB** — `AuditSink` Protocol + `SQLiteAuditSink` (SQLAlchemy 2, separate DB from
  Freqtrade's). Every risk-gate decision persisted: inputs, verdict + reason, approved amount, git
  commit SHA.
- **C. Equity curve** — periodic equity snapshot written to the audit DB on every
  `bot_loop_start` tick.
- **D. Kill switch** — file sentinel `user_data/KILL_SWITCH`; `task kill / task resume` CLI
  commands; veto checked before every entry decision; activated automatically by error budget.
- **E. Reconciliation** — peak equity restored from audit DB at startup so the drawdown circuit
  breaker is not reset to zero after a restart; >5% intra-candle equity drift triggers a warning.
- **F. Fail-safe error handling** — `run_safe[T]` wraps every external call; `ErrorBudget` with
  configurable `max_consecutive` threshold auto-activates the kill switch on sustained failures.
- **G. Telegram alerts** — `AlertSink` Protocol + `TelegramAlertSink` (stdlib urllib, zero new
  deps). Alerts on startup, circuit-breaker trips, equity drift, and error-budget exhaustion.
  Configured via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars.

**Next**: ≥ 4 weeks paper trading without crashes, erratic orders, or unexplained P&L divergence.
All kill switches must be tested manually before moving to Phase 3.

### Phase 3 — Optional AI (optional, in parallel)
- `AIAdvisor` interface + `NullAdvisor`.
- `BedrockAdvisor` behind a feature flag.
- Tests confirming that with `enabled: false` boto3 is not imported.
- Validate in backtest that adding the advisor at low weight does not degrade the system before
  increasing the weight.

### Phase 4 — Live with minimum capital (when ready, not before)
- Very small capital (symbolic amounts).
- Hard limits configured to the minimum possible.
- Active monitoring for the first weeks.
- Pre-defined criteria for scaling up or cancelling.

---

## 10. Open decisions / pending questions

Items that do not need to be decided today but are worth noting:

- **Specific exchange for paper trading**: Binance Spot Testnet is most widely used. Alternative:
  Kraken (no official testnet but Freqtrade's dry-run simulates on live market data).
- ~~**Build from scratch or use Freqtrade?**~~ **DECIDED (2026-05-26): Path A (Freqtrade).** See
  section 3.
- ~~**Run Freqtrade native or in Docker?**~~ **DECIDED (2026-05-26): native in the `uv` venv**
  (`freqtrade` as extra `[freqtrade]`). Accepts the TA-Lib system dependency and unofficial macOS
  ARM64 support in exchange for transparency and auditability. The `olibuguard.*` core remains
  independent of Freqtrade.
- **Primary timeframe**: 1h, 4h, or daily. Higher timeframes are less sensitive to microstructure
  and better for starting out.
- **Initial whitelist pairs**: probably BTC/USDT and ETH/USDT — sufficient liquidity and data.
- **Dedicated exchange account?** Recommended: separate account just for the bot, not the personal
  account where long-term holdings sit.
- **Tax policy**: if live trading ever begins, note that in Spain every trade counts as a capital
  gains event. The audit log will serve for reporting.

---

## 11. Executive summary

- **Python with strict typing**, not Go.
- **Build on Freqtrade** and add custom layers (strategy, own risk gate, optional AI, audit).
- **Three separated modes**: backtest, paper, live. Explicit and conscious mode change.
- **Risk gate as invariant module**. Strategy proposes, risk gate decides.
- **Full guardrail catalogue from day one**: hard limits, circuit breakers, multiple kill switches,
  idempotence, reconciliation, complete audit trail.
- **Local SQLite** for persistence.
- **AI behind optional interface**, default `NullAdvisor`. Bedrock only if wanted. Can never
  enlarge a trade.
- **Phased roadmap with strict exit criteria**. Live only after months of clean paper trading.
