# olibuguard

Personal crypto trading bot — **security and guardrails first, development speed second**.

Built on Freqtrade (Path A) as a personal learning project. The goal of Phase 1 is not to make
money but to produce an auditable, robust, and safe piece of software that runs in *paper trading*
for months before any real capital is risked.

> Living design document: [`docs/design.md`](docs/design.md)

---

## Status

| Phase | Description | State |
|-------|-------------|-------|
| **0 – Setup** | Hexagonal skeleton, tooling, smoke test, base guardrails | ✅ complete |
| **1 – Backtest** | Freqtrade integration (Path A), reproducible EMA 20/50 backtest | ✅ complete |
| **2 – Safety layer** | Full guardrail stack (A–G): circuit breakers, audit DB, kill-switch, reconciliation, fail-safe error budget, Telegram alerts | ✅ complete |
| **3 – Optional AI** | AWS Bedrock `AIAdvisor` behind feature flag | ⏳ pending |
| **4 – Live (min. capital)** | Only after ≥ 4 weeks of clean paper trading | ⏳ pending |

**Next milestone**: 4-week paper-trading window (Freqtrade dry-run against live Binance market data).

---

## Core philosophy

- **Three hard-separated modes**: `backtest`, `paper` (dry-run, default), `live`.
  Mode is fixed at startup; switching to `live` requires the explicit flag
  `--i-understand-this-is-real-money`.
- **Invariant risk gate**: the strategy *proposes*, the risk gate *decides*.
  Every order passes through: dynamic sizing as % of equity, circuit breakers (drawdown + daily
  loss), slippage guard, pair whitelist/blacklist, order-rate limit, and minimum notional.
- **Pluggable AI**: `NullAdvisor` by default; the advisor can only **veto or reduce** a trade,
  never initiate or enlarge it.
- **Secrets outside code**: `keyring` + `.env` via `python-dotenv`. Keys never in the repo.

---

## Phase 2 safety layers (all implemented)

| Layer | What it does | Key files |
|-------|--------------|-----------|
| **A – Circuit breakers** | `PortfolioState` fed with real equity, peak, and daily PnL; drawdown + daily-loss trips halt new entries | `risk/gate.py`, `OlibuguardStrategy.py` |
| **B – Audit DB** | Every risk-gate decision persisted to a separate SQLite DB (SQLAlchemy 2, Decimal-safe TEXT columns) | `audit/` |
| **C – Equity curve** | Periodic equity snapshot written to the audit DB on every candle | `audit/sqlite.py` |
| **D – Kill switch** | File sentinel `user_data/KILL_SWITCH`; `task kill / task resume`; auto-activated by error budget | `kill_switch.py`, `cli.py` |
| **E – Reconciliation** | Peak equity restored from audit DB at startup so the drawdown circuit breaker survives restarts; >5% intra-candle drift triggers a warning | `reconciliation.py`, `audit/sink.py` |
| **F – Fail-safe error budget** | `run_safe` wraps every external call; `ErrorBudget` auto-activates the kill switch after 5 consecutive wallet-read failures | `failsafe.py` |
| **G – Telegram alerts** | Startup notice, circuit-breaker trips, equity drift, error-budget exhaustion; stdlib `urllib`, zero new deps | `alerts/` |

---

## Architecture (hexagonal)

The `olibuguard.*` core has no knowledge of Freqtrade or any exchange. Freqtrade is the *runner*
(exchange connectivity, dry-run, backtesting, persistence). `OlibuguardStrategy` is a thin adapter
that translates between Freqtrade's float world and the core's `Decimal` domain, wiring the risk
gate into `custom_stake_amount` (sizing) and `confirm_trade_entry` (veto). If you ever need to
migrate off Freqtrade, only the adapter changes.

```
olibuguard/                      # repo root
├── Taskfile.yml                 # task runner (thin wrapper over uv)
├── pyproject.toml               # uv, deps, ruff, mypy --strict, pytest
│
├── olibuguard/                  # Python package — core (Freqtrade-agnostic)
│   ├── alerts/                  # AlertSink Protocol · NullAlertSink · TelegramAlertSink
│   ├── audit/                   # DecisionAudit · EquityPoint · AuditSink · AuditReader · SQLiteAuditSink
│   ├── advisor/                 # AIAdvisor Protocol · NullAdvisor (default)
│   ├── domain/                  # OrderIntent · PortfolioState · RiskVerdict · Side
│   ├── risk/                    # RiskGate — the invariant safety core
│   ├── cli.py                   # CLI: smoke · run · kill · resume
│   ├── config.py                # AppConfig · RiskLimits (pydantic)
│   ├── failsafe.py              # run_safe[T] · ErrorBudget
│   ├── kill_switch.py           # KillSwitch — file-based sentinel
│   ├── logging.py               # structured logging (structlog)
│   ├── modes.py                 # Mode enum: backtest | paper | live
│   ├── orchestrator.py          # main loop skeleton
│   ├── reconciliation.py        # restore_peak_equity · check_equity_drift
│   └── secrets.py               # keyring-backed secret access
│
├── user_data/                   # Freqtrade workspace
│   ├── config.json              # Freqtrade config (dry-run, pairs, fees, protections)
│   └── strategies/
│       └── OlibuguardStrategy.py  # IStrategy adapter → olibuguard core
│
├── tests/                       # pytest + hypothesis (76 tests)
├── docs/design.md               # living design document
├── .skills/                     # project audit rubric
├── Dockerfile                   # deployment image (Freqtrade + olibuguard)
├── docker-compose.yml           # 24/7 dry-run daemon
├── config.example.yaml          # core config template (copy → config.yaml)
└── .env.example                 # secrets template  (copy → .env)
```

---

## Requirements

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | resolved automatically by `uv` |
| [`uv`](https://docs.astral.sh/uv/) | latest | environment + dependency manager (`uv.lock`) |
| [`Task`](https://taskfile.dev) | 3.50+ | task runner — `brew install go-task` |
| TA-Lib | system lib | required by Freqtrade |

---

## Installation

`task` orchestrates `uv` underneath; `uv` remains the dependency manager and lock-file owner.

**macOS (Apple Silicon):**
```bash
brew install ta-lib go-task
task install          # uv sync --extra freqtrade with TA-Lib paths pre-set
```

**Windows:** install *Desktop development with C++* from the VC++ Build Tools, then `task install`.

**Core only** (no Freqtrade, no TA-Lib): `uv sync`

---

## Commands

Run `task` to list all available tasks.

**Quality gate:**
```bash
task check        # lint + typecheck + tests (full gate — must stay green)
task lint         # ruff check
task typecheck    # mypy --strict (32 source files)
task test         # pytest (76 tests, includes hypothesis property-based)
```

**CLI:**
```bash
task smoke                                             # read config + self-check the risk gate
task run -- --mode paper                               # start in paper (dry-run)
task run -- --mode live --i-understand-this-is-real-money   # live requires explicit confirmation
```

**Kill switch:**
```bash
task kill                          # activate — bot stops opening new positions immediately
task kill -- --reason "suspicious" # with a reason recorded in the sentinel file
task resume                        # deactivate — bot resumes on the next candle
```

**Freqtrade backtest:**
```bash
task download -- --timerange 20250101-20250401
task backtest -- --timerange 20250101-20250401
```

**Paper trading (native, foreground):**
```bash
task paper          # Freqtrade trade --dry-run, logs to terminal
```

**Docker (24/7 dry-run daemon — recommended for the 4-week window):**
```bash
task docker-up      # build + start daemon; FreqUI at http://localhost:8080
task docker-logs    # tail logs
task docker-down    # graceful stop
```

---

## Paper trading — start-up guide

This is the **Phase 2 exit criterion**: ≥ 4 weeks of dry-run against live Binance market data
without crashes, erratic orders, or unexplained P&L divergence.

### 1. Get a Binance Spot Testnet API key
Go to **https://testnet.binance.vision/** (GitHub login) and generate a key pair.
Testnet keys are free, isolated from real funds, and hard-wired in `config.json` (`sandbox: true`
+ explicit testnet URLs so ccxt always routes to `testnet.binance.vision`).

### 2. Configure `.env`
```bash
cp .env.example .env
```
Fill in at minimum:
```bash
FREQTRADE__EXCHANGE__KEY=<your-key>
FREQTRADE__EXCHANGE__SECRET=<your-secret>

# Optional — strongly recommended for 4 weeks of unattended monitoring:
TELEGRAM_BOT_TOKEN=<token from @BotFather>
TELEGRAM_CHAT_ID=<your chat ID>

# FreqUI credentials (change from the placeholder defaults):
FREQTRADE__API_SERVER__PASSWORD=<your-password>
FREQTRADE__API_SERVER__JWT_SECRET_KEY=<long-random-string>
```

### 3. Start the daemon
```bash
task docker-up
```
First run builds the image (~2 min). Subsequent starts are instant.

### 4. Verify it is running
Open **http://localhost:8080** — FreqUI shows open trades, equity curve, and recent decisions.
Log in with username `olibuguard` and the password you set in `.env`.

```bash
task docker-logs    # live log stream; look for "olibuguard started" at the top
```

### 5. Test the kill switch (required before Phase 3)
```bash
task kill -- --reason "kill-switch pre-flight test"
# verify: FreqUI shows no new entries opening
task resume
# verify: bot resumes on the next candle
```

### 6. Watch for 4 weeks
The audit DB (`user_data/olibuguard_audit.sqlite`) records every decision. After the window:
- No crashes or unhandled exceptions in the logs.
- No unexplained equity jumps (audit DB drift checks are your early warning).
- Circuit breakers tested at least once (use `task kill/resume` to simulate).

When criteria are met → Phase 3 (optional AI) or Phase 4 (live with minimum capital).

---

## Configuration

### Core config (`config.example.yaml` → `config.yaml`)
Risk limits and AI settings for the olibuguard core. Copy and adjust:
```bash
cp config.example.yaml config.yaml
```

### Secrets (`.env.example` → `.env`)
Never commit `.env`. It is listed in `.gitignore`.
```bash
cp .env.example .env   # then fill in your values
```

### Freqtrade config (`user_data/config.json`)
Pairs, fees, protections, Freqtrade API server settings. Already configured for dry-run with
BTC/USDT + ETH/USDT on the 1h timeframe.

### Telegram alerts (optional)
Set two environment variables (in `.env` or system env):
```bash
TELEGRAM_BOT_TOKEN=<token from @BotFather>
TELEGRAM_CHAT_ID=<your chat ID>
```
When present, the bot sends a startup notice, circuit-breaker trips, equity drift warnings,
and error-budget exhaustion alerts. No extra dependencies — uses stdlib `urllib`.

---

## Project standards (`.skills/`)

Code is audited against five rubrics in `.skills/`:

| Rubric | Enforces |
|--------|----------|
| `trading_guardrails.md` | Capital protection (CRO mindset): position sizing, circuit breakers, kill-switch, simulation mode |
| `senior_python_dev.md` | Production-quality Python: strict typing, error handling, structured logging, vectorised data ops |
| `backtesting_specialist.md` | Zero look-ahead bias, real costs (fees + slippage), professional metrics (Sharpe, Sortino, max drawdown) |
| `security_devops.md` | Secret management, state persistence, containerisation, health checks |
| `language_standards.md` | All code, docs, logs, and comments in English |

**Tooling invariants**: `ruff` (lint + format), `mypy --strict` (type checking), `pytest` +
`hypothesis` (tests), `uv` (deps), `task` (runner). CI gate: `task check` must be green before
every commit.
