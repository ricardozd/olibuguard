# Olibuguard — Operations

## Starting the bot (Docker)

### Paper mode (no real money)

```bash
# 1. Authenticate with AWS (session valid ~12 h, required for the AI advisor)
aws login

# 2. Export STS credentials to .env
task aws-refresh

# 3. Build + start as daemon in paper mode
task paper-up

# 4. Release from STOPPED state (Freqtrade starts paused for safety)
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

### Live mode (real orders on Binance)

```bash
# 1. AWS credentials
aws login
task aws-refresh

# 2. Build + start as daemon in live mode
task docker-up

# 3. Release from STOPPED state
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

The only difference between modes is the `FREQTRADE__DRY_RUN` variable injected by the task.
The Docker image and strategy are identical.

The bot runs 24/7 thanks to `restart: unless-stopped` in docker-compose.

---

## Web dashboard (FreqUI)

Available at **http://localhost:8081** (localhost only — never exposed to the network).

Credentials: `olibuguard` / password in `.env`

---

## Real-time logs

```bash
task docker-logs
```

---

## Stop the bot

```bash
task docker-down
```

---

## Renew AWS credentials (every ~12 h)

STS tokens expire. To renew without losing trade state:

```bash
aws login           # if the host session also expired
task aws-refresh    # writes new tokens to .env

# IMPORTANT: `restart` does NOT reload env vars from the compose file — always use down + up
docker compose down
task paper-up       # or task docker-up depending on the mode
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

> `docker compose restart` does **not** pick up changes to environment variables.
> Always use `down` + `paper-up` / `docker-up`.

---

## Emergency kill switch

Stops **new entries** immediately — open positions are untouched:

```bash
task kill      # activate
task resume    # deactivate
```

---

## Local development (no Docker)

```bash
# Install dependencies
task install

# Tests, lint, typecheck
task check

# Paper trading natively (no Docker, live market data, no real orders)
task paper
```

---

## Backtesting

```bash
# Download historical OHLCV data (example: Q1 2025)
task download -- --timerange 20250101-20250401

# Run backtest
task backtest -- --timerange 20250101-20250401
```

---

## Querying the audit trail

```bash
# Last 20 risk gate decisions
sqlite3 user_data/olibuguard_audit.sqlite \
  "SELECT at, symbol, approved, reason FROM audit_log ORDER BY at DESC LIMIT 20;"

# Last 10 equity snapshots
sqlite3 user_data/olibuguard_audit.sqlite \
  "SELECT at, equity_quote FROM equity_curve ORDER BY at DESC LIMIT 10;"
```

---

## Key files

| File | Purpose |
|------|---------|
| `user_data/config.json` | Freqtrade config (exchange, pairs, ROI, stoploss) |
| `user_data/config.yaml` | olibuguard config (risk limits, AI advisor) |
| `.env` | Secrets and credentials (NEVER commit) |
| `user_data/strategies/OlibuguardStrategy.py` | Freqtrade → core adapter |
| `user_data/olibuguard_audit.sqlite` | Decision and equity audit trail |
| `user_data/KILL_SWITCH` | Kill switch sentinel file (exists = active) |

---

## Relevant environment variables

| Variable | Purpose |
|----------|---------|
| `OLIBUGUARD_CONFIG` | Path to `config.yaml` |
| `FREQTRADE__EXCHANGE__KEY/SECRET` | Exchange API keys |
| `AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY/SESSION_TOKEN` | STS credentials for Bedrock (via `task aws-refresh`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram alerts |
