# olibuguard

Bot de trading de criptomonedas para correr en local, con la **seguridad y los guardarraíles por delante de la velocidad de desarrollo**. Es un proyecto personal de aprendizaje: el objetivo de la fase 1 no es ganar dinero, sino tener una pieza de software auditable, robusta y segura que opere en *paper trading* durante meses antes de plantearse capital real.

> Documento de diseño completo y vivo: [`docs/diseno.md`](docs/diseno.md).

## Estado

- **Fase 0 (setup):** ✅ completa — tooling, esqueleto hexagonal, guardarraíles base, smoke test.
- **Fase 1 (backtest end-to-end):** 🔶 arrancada — integración con Freqtrade (Camino A) y backtest reproducible con la estrategia de ejemplo EMA20/50.
- **Fases 2–4** (paper trading prolongado, IA opcional, live con cap mínimo): pendientes. Ver roadmap en el doc de diseño.

## Filosofía y guardarraíles

- **Tres modos separados y excluyentes:** `backtest`, `paper` (dry-run, por defecto), `live`. El modo se fija al arrancar; pasar a `live` exige un flag de confirmación explícito (`--i-understand-this-is-real-money`).
- **Risk gate invariante:** la estrategia *propone*, el risk gate *dispone*. Antes de cada orden aplica:
  - Sizing dinámico como **% del capital** (`max_risk_per_trade_pct`) + caps absolutos.
  - **Circuit breakers**: límite de pérdida diaria y drawdown desde el pico de equity.
  - Guard de **slippage**, whitelist/blacklist de pares, máximo de órdenes por minuto y mínimo nocional.
- **Kill-switch en runtime** vía `protections` de Freqtrade (MaxDrawdown, StoplossGuard, CooldownPeriod), como segunda red.
- **IA desenchufable:** `NullAdvisor` por defecto; el advisor solo puede **vetar o reducir** una operación, nunca agrandarla.
- **Secretos fuera del código:** `keyring` + `.env` (`python-dotenv`). Nunca claves en el repo.

## Arquitectura (hexagonal)

El núcleo `olibuguard.*` no conoce el exchange ni Freqtrade. Freqtrade es el *runner* (conexión a exchange, dry-run, backtesting, persistencia) y una clase adaptadora (`OlibuguardStrategy`) delega en el núcleo: las señales salen de la estrategia, y el risk gate se engancha en `custom_stake_amount` (dimensionado) y `confirm_trade_entry` (veto). Así se mantiene la opción de migrar fuera de Freqtrade.

```
olibuguard/                  # repo (el proyecto)
├── olibuguard/              # paquete Python (núcleo, agnóstico a Freqtrade)
│   ├── cli.py               # CLI: smoke, run (modos + confirmación live)
│   ├── config.py            # config con pydantic (RiskLimits, AIConfig, ...)
│   ├── modes.py             # Mode: backtest | paper | live
│   ├── logging.py           # logging estructurado (structlog)
│   ├── secrets.py           # acceso a secretos vía keyring
│   ├── orchestrator.py      # loop: market data -> strategy -> risk gate -> order mgr
│   ├── domain/              # models.py (tipos) + ports.py (interfaces Protocol)
│   ├── risk/                # gate.py (el risk gate invariante)
│   └── advisor/             # base.py (AIAdvisor + NullAdvisor por defecto)
├── user_data/               # Freqtrade
│   ├── config.json          # config de Freqtrade (dry-run, pares, fees, alertas)
│   └── strategies/OlibuguardStrategy.py   # adaptador IStrategy -> núcleo
├── tests/                   # pytest (+ hypothesis para el risk gate)
├── docs/diseno.md           # documento de diseño vivo
├── .skills/                 # rúbrica de auditoría del proyecto
├── pyproject.toml           # uv, dependencias, ruff, mypy --strict, pytest
├── Dockerfile               # imagen de despliegue (Freqtrade + olibuguard)
├── docker-compose.yml       # demonio dry-run 24/7
├── config.example.yaml      # plantilla de config del núcleo
└── .env.example             # plantilla de secretos (copiar a .env)
```

## Requisitos

- Python 3.12+ (lo resuelve `uv`).
- [`uv`](https://docs.astral.sh/uv/) para gestionar el entorno y las dependencias.
- TA-Lib (librería de sistema) para Freqtrade.

## Instalación (nativa)

**macOS (Apple Silicon):**

```bash
brew install ta-lib
export TA_INCLUDE_PATH=/opt/homebrew/opt/ta-lib/include
export TA_LIBRARY_PATH=/opt/homebrew/opt/ta-lib/lib
export PKG_CONFIG_PATH=/opt/homebrew/opt/ta-lib/lib/pkgconfig
uv sync --extra freqtrade
```

**Windows:** instalar las "VC++ Build Tools" (Desktop development with C++) y luego `uv sync --extra freqtrade`.

**Solo el núcleo** (sin Freqtrade, para desarrollar/testear la lógica): `uv sync`.

## Uso

**Calidad de código:**

```bash
uv run ruff check .     # lint
uv run mypy             # tipado estricto
uv run pytest           # tests (incluye property-based del risk gate)
```

**CLI:**

```bash
uv run olibuguard smoke              # arranca, lee config, autochequea el risk gate y sale
uv run olibuguard run --mode paper   # arranca en paper (dry-run)
uv run olibuguard run --mode live --i-understand-this-is-real-money   # live exige confirmación
```

**Backtest con Freqtrade:**

```bash
uv run freqtrade download-data --userdir user_data --config user_data/config.json \
  --pairs BTC/USDT ETH/USDT --timeframes 1h --timerange 20250101-20250401

uv run freqtrade backtesting --userdir user_data --config user_data/config.json \
  --strategy OlibuguardStrategy --timerange 20250101-20250401 --enable-protections
```

**Docker (despliegue dry-run 24/7):**

```bash
docker compose up --build -d
```

## Configuración

- `config.example.yaml` → copiar a `config.yaml` (config del núcleo: límites de riesgo e IA).
- `.env.example` → copiar a `.env` (secretos; está en `.gitignore`).
- `user_data/config.json` → config de Freqtrade (dry-run, pares, fees, protections, alertas Telegram).

## Estándares del proyecto (`.skills/`)

El código se audita estrictamente contra las reglas en `.skills/`: protección de capital (CRO), Python senior cuantitativo, backtesting riguroso, DevSecOps y estándares bilingües (código en inglés, comunicación en español).
