# olibuguard

Bot de trading automático de criptomonedas — **seguridad y guardrails primero**.

Construido sobre Freqtrade como proyecto personal de aprendizaje. Arquitectura hexagonal en Python:
el core (`olibuguard.*`) no conoce Freqtrade; `OlibuguardStrategy` es el adaptador.

---

## Estado actual

| Fase | Descripción | Estado |
|------|-------------|--------|
| **0 – Setup** | Esqueleto hexagonal, tooling, smoke test, guardrails base | ✅ completo |
| **1 – Estrategia** | Integración Freqtrade, señal EMA 20/50, backtest reproducible | ✅ completo |
| **2 – Safety layer** | Circuit breakers, audit DB, kill-switch, reconciliación, error budget, alertas Telegram | ✅ completo |
| **3 – AI Advisor** | AWS Bedrock (Claude Opus 4 + extended thinking) como veto-only advisor | ✅ completo |
| **4 – Live** | Binance real con capital mínimo | 🟡 en curso |

**Ahora mismo**: corriendo en paper mode (5m, BTC/USDT + ETH/USDT) con AI advisor activo.

---

## Filosofía de diseño

- **Invariant risk gate**: la estrategia propone, el risk gate decide. Toda orden pasa por: sizing dinámico (% equity), circuit breakers, slippage guard, whitelist/blacklist, rate limit y notional mínimo.
- **AI veto-only**: el advisor solo puede rechazar una operación, nunca iniciarla ni ampliarla. Cualquier fallo de Bedrock → `NullAdvisor` → la operación continúa (fail-safe).
- **Doble stoploss**: ATR dinámico (stop = precio - ATR×2) + orden enviada directamente a Binance (`stoploss_on_exchange`). Si el bot cae, Binance ejecuta el stop.
- **Secretos fuera del código**: credenciales via `.env`. AWS via tokens STS de 12h (`task aws-refresh`), nunca claves permanentes.

---

## Arranque rápido (Docker)

```bash
# 1. Credenciales AWS para el AI advisor (tokens STS, válidos ~12 h)
aws login
task aws-refresh

# 2. Paper mode (sin dinero real)
task paper-up
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!

# 3. Live mode (cuando tengas USDT en Binance)
task docker-up
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

**FreqUI**: http://localhost:8081 — `olibuguard` / password en `.env`

---

## Cómo opera el bot

**Señal**: EMA 20 cruza por encima de EMA 50 en vela de 5m → candidato de compra.

**Flujo de decisión**:
```
Señal EMA → confirm_trade_entry()
    ├── Kill switch activo?          → RECHAZA
    ├── AI Advisor (Claude Opus 4)   → ¿veto?   → RECHAZA  (fail-safe si falla)
    └── RiskGate
            ├── Circuit breakers (drawdown ≥10% o pérdida diaria ≥5%) → RECHAZA
            ├── Whitelist / blacklist
            ├── Rate limit órdenes/minuto
            ├── Slippage excesivo
            ├── Sizing: mín(2% equity, 50 USDT)
            └── Notional mínimo 10 USDT
```

**Stoploss**: dinámico por ATR (se adapta a la volatilidad del momento). Suelo fijo en -10%.

**Salida**: cruce inverso EMA + `minimal_roi` (10%) + stoploss.

---

## Safety layers (Fase 2)

| Capa | Qué hace |
|------|----------|
| **A – Circuit breakers** | Drawdown ≥ 10% o pérdida diaria ≥ 5% → para nuevas entradas |
| **B – Audit DB** | Cada decisión del risk gate persiste en SQLite (`olibuguard_audit.sqlite`) |
| **C – Equity curve** | Snapshot de equity cada 5 minutos |
| **D – Kill switch** | Fichero centinela `KILL_SWITCH`; `task kill` / `task resume` |
| **E – Reconciliación** | Peak equity restaurado desde DB al arrancar; drift >5% genera alerta |
| **F – Error budget** | 5 fallos consecutivos de lectura del wallet → kill switch automático |
| **G – Telegram** | Alertas de arranque, circuit breaker, drift, error budget |

---

## Comandos útiles

```bash
task            # listar todos los tasks disponibles

# Calidad
task check      # lint + typecheck + tests (gate completo)

# Docker
task paper-up   # paper mode (dry_run=true)
task docker-up  # live mode  (dry_run=false)
task docker-down
task docker-logs

# AWS (renovar cada ~12 h)
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

## Estructura

```
olibuguard/
├── Taskfile.yml                    # task runner
├── Dockerfile / docker-compose.yml # despliegue 24/7
├── pyproject.toml                  # deps, ruff, mypy --strict, pytest
│
├── olibuguard/                     # core (sin dependencias de Freqtrade)
│   ├── advisor/    bedrock.py      # BedrockAdvisor (Claude Opus 4, veto-only)
│   ├── audit/      sqlite.py       # DecisionAudit · EquityPoint · SQLiteAuditSink
│   ├── alerts/     telegram.py     # TelegramAlertSink
│   ├── domain/     models.py       # OrderIntent · PortfolioState · RiskVerdict
│   ├── risk/       gate.py         # RiskGate — núcleo invariante
│   ├── config.py                   # AppConfig · RiskLimits · AIConfig (pydantic)
│   ├── failsafe.py                 # run_safe · ErrorBudget
│   ├── kill_switch.py              # KillSwitch
│   └── reconciliation.py           # restore_peak_equity · check_equity_drift
│
├── user_data/
│   ├── config.json                 # Freqtrade (pares, ROI, stoploss, protections)
│   ├── config.yaml                 # olibuguard (risk limits, AI advisor) — no commitear
│   └── strategies/
│       └── OlibuguardStrategy.py   # adaptador IStrategy → core
│
├── docs/
│   ├── overview.md                 # qué hace el bot y cómo opera
│   ├── architecture.md             # diseño hexagonal, módulos, flujo de datos
│   └── operations.md               # runbook: arrancar, renovar AWS, backtesting
│
└── tests/                          # pytest + hypothesis
```

---

## Requisitos

| Herramienta | Instalación |
|-------------|-------------|
| Python 3.12+ | vía `uv` |
| [`uv`](https://docs.astral.sh/uv/) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [`Task`](https://taskfile.dev) | `brew install go-task` |
| TA-Lib | `brew install ta-lib` (macOS) |
| Docker Desktop | para el modo daemon 24/7 |

```bash
brew install ta-lib go-task
task install
task check      # debe quedar verde
```
