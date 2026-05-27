# olibuguard

Automated crypto trading bot — **safety and guardrails first**.

Built on top of Freqtrade as a personal learning project. Hexagonal architecture in Python:
the core (`olibuguard.*`) has no knowledge of Freqtrade; `OlibuguardStrategy` is the adapter.

---

## Current status

| Phase | Description | Status |
|-------|-------------|--------|
| **0 – Setup** | Hexagonal skeleton, tooling, smoke test, base guardrails | ✅ done |
| **1 – Strategy** | Freqtrade integration, EMA 20/50 signal, reproducible backtest | ✅ done |
| **2 – Safety layer** | Circuit breakers, audit DB, kill-switch, reconciliation, error budget, Telegram alerts | ✅ done |
| **3 – AI Advisor** | AWS Bedrock (Claude Opus 4 + extended thinking) as veto-only advisor | ✅ done |
| **4 – Live** | Real Binance orders with minimal capital | 🟡 in progress |

**Right now**: running in paper mode (5m, BTC/USDT + ETH/USDT) with AI advisor active.

---

## Design philosophy

- **Invariant risk gate**: the strategy proposes, the risk gate decides. Every order passes through: dynamic sizing (% equity), circuit breakers, slippage guard, whitelist/blacklist, rate limit, and minimum notional.
- **AI veto-only**: the advisor can only reject a trade, never initiate or enlarge it. Any Bedrock failure → `NullAdvisor` → trade proceeds (fail-safe).
- **Double stoploss**: dynamic ATR (stop = price − ATR×2) + order sent directly to Binance (`stoploss_on_exchange`). If the bot goes down, Binance still executes the stop.
- **Secrets outside the codebase**: credentials via `.env`. AWS via 12h STS tokens (`task aws-refresh`), never permanent keys.

---

## Quick start (Docker)

```bash
# 1. AWS credentials for the AI advisor (STS tokens, valid ~12 h)
aws login
task aws-refresh

# 2. Paper mode (no real money)
task paper-up
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!

# 3. Live mode (when you have USDT on Binance)
task docker-up
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

**FreqUI**: http://localhost:8081 — `olibuguard` / password in `.env`

---

## How the bot operates

**Signal**: EMA 20 crosses above EMA 50 on a 5m candle → buy candidate.

**Decision flow**:
```
EMA signal → confirm_trade_entry()
    ├── Kill switch active?              → REJECT
    ├── AI Advisor (Claude Opus 4)       → veto?  → REJECT  (fail-safe if error)
    └── RiskGate
            ├── Circuit breakers (drawdown ≥10% or daily loss ≥5%) → REJECT
            ├── Whitelist / blacklist
            ├── Rate limit orders/minute
            ├── Excessive slippage
            ├── Sizing: min(2% equity, 50 USDT)
            └── Minimum notional 10 USDT
```

**Stoploss**: dynamic ATR (adapts to current volatility). Hard floor at −10%.
Binance holds a stop order directly — survives bot downtime.

**Exit**: reverse EMA cross + `minimal_roi` (10%) + stoploss.

---

## Safety layers (Phase 2)

| Layer | What it does |
|-------|-------------|
| **A – Circuit breakers** | Drawdown ≥ 10% or daily loss ≥ 5% → halt new entries |
| **B – Audit DB** | Every risk gate decision persisted in SQLite (`olibuguard_audit.sqlite`) |
| **C – Equity curve** | Equity snapshot every 5 minutes |
| **D – Kill switch** | Sentinel file `KILL_SWITCH`; `task kill` / `task resume` |
| **E – Reconciliation** | Peak equity restored from DB on startup; drift >5% triggers alert |
| **F – Error budget** | 5 consecutive wallet-read failures → automatic kill switch |
| **G – Telegram** | Alerts on startup, circuit breaker trigger, equity drift |

---

## Useful commands

```bash
task            # list all available tasks

# Quality gate
task check      # lint + typecheck + tests

# Docker
task paper-up   # paper mode (dry_run=true)
task docker-up  # live mode  (dry_run=false)
task docker-down
task docker-logs

# AWS (renew every ~12 h)
task aws-refresh

# Kill switch
task kill
task resume

# Backtest
task download -- --timerange 20250101-20250401
task backtest -- --timerange 20250101-20250401

# Audit DB
sqlite3 user_data/olibuguard_audit.sqlite \
  "SELECT at, symbol, approved, reason FROM audit_log ORDER BY at DESC LIMIT 20;"
sqlite3 user_data/olibuguard_audit.sqlite \
  "SELECT at, equity_quote FROM equity_curve ORDER BY at DESC LIMIT 10;"
```

---

## Project structure

```
olibuguard/
├── Taskfile.yml                    # task runner
├── Dockerfile / docker-compose.yml # 24/7 daemon deployment
├── pyproject.toml                  # deps, ruff, mypy --strict, pytest
│
├── olibuguard/                     # core (no Freqtrade dependencies)
│   ├── advisor/    bedrock.py      # BedrockAdvisor (Claude Opus 4, veto-only)
│   ├── audit/      sqlite.py       # DecisionAudit · EquityPoint · SQLiteAuditSink
│   ├── alerts/     telegram.py     # TelegramAlertSink
│   ├── domain/     models.py       # OrderIntent · PortfolioState · RiskVerdict
│   ├── risk/       gate.py         # RiskGate — invariant core
│   ├── config.py                   # AppConfig · RiskLimits · AIConfig (pydantic)
│   ├── failsafe.py                 # run_safe · ErrorBudget
│   ├── kill_switch.py              # KillSwitch
│   └── reconciliation.py          # restore_peak_equity · check_equity_drift
│
├── user_data/
│   ├── config.json                 # Freqtrade (pairs, ROI, stoploss, protections)
│   ├── config.yaml                 # olibuguard (risk limits, AI advisor) — do not commit
│   └── strategies/
│       └── OlibuguardStrategy.py   # IStrategy adapter → core
│
├── docs/
│   ├── overview.md                 # what the bot does and how it operates
│   ├── architecture.md             # hexagonal design, modules, data flow
│   ├── operations.md               # runbook: start, renew AWS, backtesting
│   └── roadmap.md                  # path to $300–500/month goal
│
└── tests/                          # pytest + hypothesis
```

---

## Requirements

| Tool | Installation |
|------|-------------|
| Python 3.12+ | via `uv` |
| [`uv`](https://docs.astral.sh/uv/) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [`Task`](https://taskfile.dev) | `brew install go-task` |
| TA-Lib | `brew install ta-lib` (macOS) |
| Docker Desktop | for 24/7 daemon mode |

```bash
brew install ta-lib go-task
task install
task check      # must be green
```
