# Bot de Trading Cripto — Diseño y Decisiones

Documento de arranque. Recoge la visión, decisiones técnicas y los guardarraíles que se aplican al proyecto. Vivo: se actualiza según evoluciona el bot.

Fecha: 2026-05-26
Autor: Oliv (con asistencia de Claude)

---

## 1. Visión y restricciones

Construir un bot de trading de criptomonedas para correr en local en Windows, como proyecto personal de aprendizaje y experimentación. El objetivo de la fase 1 no es ganar dinero, sino tener una pieza de software auditable, robusta y segura, capaz de operar en modo simulación (paper trading) durante un periodo de validación largo antes de plantearse tocar capital real.

Restricciones que condicionan todo el diseño:

- **Seguridad por encima de velocidad de desarrollo.** Cualquier disyuntiva se resuelve a favor de robustez, trazabilidad y "fail-safe".
- **Paper trading primero.** Ninguna línea de código en modo "live" hasta que el bot lleve semanas/meses operando sin fallos en simulación.
- **Guardarraíles preparados desde el día uno.** Los límites de riesgo no son una mejora futura, son parte de la arquitectura mínima.
- **IA desenchufable.** Cualquier integración con AWS Bedrock (u otro LLM) tiene que poder desactivarse con un flag, sin que falte funcionalidad core.
- **Local en Windows y mac.** Sin dependencias de cloud para el ciclo principal de decisión. Persistencia en disco local.

---

## 2. Lenguaje: Python vs Go

He sopesado las dos opciones porque el usuario las tenía sobre la mesa. La recomendación es **Python con tipado estricto**, no Go.

### Por qué Python gana aquí

El ecosistema de cripto/finanzas en Python está a años luz del de Go. En 2026 las piezas clave siguen siendo:

- **`ccxt` 4.5.x** — librería de referencia para hablar con 100+ exchanges (Binance, Kraken, Coinbase incluidos). Mantenimiento muy activo.
- **`Freqtrade` 2026.4** — framework FOSS para bots de cripto. Soporta nativamente paper trading (dry-run), backtesting y optimización de hiperparámetros. Es lo que más cerca está de lo que queremos construir.
- **`vectorbt` 1.0** — backtesting vectorizado moderno. (`backtrader` está abandonado desde 2023, no usarlo).
- **`pandas`, `numpy`, `TA-Lib`** — análisis técnico maduro.
- **`pydantic`** — validación en tiempo de ejecución para configs y mensajes.
- **`boto3`** — SDK oficial AWS para integración con Bedrock cuando/si se quiera.

El argumento de "Go es compilado y por tanto más seguro" no se sostiene una vez que en Python aplicas:

- `mypy --strict` o `pyright strict` en CI local.
- `pydantic` para validar configs y eventos en runtime.
- Tipado completo del dominio (sin `Any`).
- `pytest` con cobertura amplia, especialmente del módulo de riesgo.

Con eso Python da garantías muy parecidas a las de un compilado para este tipo de aplicación, y el coste es muchísimo menor.

### Cuándo cambiaría a Go

Solo si en algún momento aparece una necesidad real de latencia muy baja (scalping, market making competitivo). Para swing/day trading con velas de minutos u horas, la latencia de Python es irrelevante. La decisión queda revisable, pero la apuesta inicial es Python.

### Stack concreto recomendado

```
Python 3.12+
- ccxt (acceso a exchanges)
- pandas / numpy (datos)
- pydantic v2 (validación)
- SQLAlchemy 2 + SQLite (persistencia)
- structlog (logging estructurado JSON)
- rich (CLI bonita) o Textual (TUI si quiero monitor)
- pytest + hypothesis (tests)
- mypy --strict (tipado en CI local)
- ruff (lint y formato)
```

Herramientas de proyecto: `uv` o `poetry` para dependencias (uv es más rápido). Pre-commit hook con ruff + mypy.

---

## 3. ¿Construir desde cero o partir de Freqtrade?

Hay dos caminos honestos:

### Camino A — Construir sobre Freqtrade (recomendado)

Freqtrade ya resuelve, probado en producción por miles de usuarios:

- Conexión a exchanges (vía ccxt).
- Dry-run / paper trading nativo.
- Backtesting y hyperopt.
- Sistema de estrategias custom en Python.
- Persistencia (SQLite por defecto).
- API REST + Telegram para control y kill-switch remoto.
- Stop-loss, trailing stop, take-profit, position sizing.

Sobre esa base, lo que añado es:

- Mi capa de **risk gate** propia que envuelve las órdenes (segunda red de seguridad además de la de Freqtrade).
- Mis **estrategias** custom.
- El módulo de **AI advisor** opcional (Bedrock) detrás de una interfaz.
- Mi propio sistema de **alertas y auditoría**.

Ventaja: empiezo con los guardarraíles que ya están battle-tested, no los reinvento. Foco en lo que aporta valor único (estrategia, IA opcional, supervisión).

### Camino B — Construir desde cero con ccxt

Más control absoluto, más aprendizaje profundo, pero también más código, más bugs propios y más tiempo hasta tener algo seguro. Reinventar order management, persistencia, backtest, dry-run, etc.

### Recomendación

**Empezar con A (Freqtrade) y mantener la opción de salir.** La capa de estrategias y la de risk gate las escribo de forma que no estén casadas con Freqtrade — si en algún momento quiero migrar a stack propio, puedo. Pero arrancar pegándome con la fontanería de exchange es una distracción del objetivo de aprendizaje, que es la lógica de trading y los guardarraíles.

### Decisión (2026-05-26): Camino A — Freqtrade

Confirmado. Notas de la verificación previa:

- **Versión y Python**: Freqtrade `2026.4` soporta Python 3.11–3.14, así que el venv actual (3.14.4) sirve. En macOS ARM64 Freqtrade recomienda Docker; instalación nativa posible pero no soportada oficialmente. Windows nativo va con las VC++ build tools. TA-Lib sigue siendo dependencia de sistema.

**Cómo integramos sin casarnos con Freqtrade**: una clase `OlibuguardStrategy(IStrategy)` actúa de adaptador fino y delega en el núcleo `olibuguard.*` (que no importa Freqtrade). Mapa de enganches:

| Hook de Freqtrade (`IStrategy`) | Qué pone olibuguard |
|---|---|
| `populate_indicators` / `populate_entry_trend` / `populate_exit_trend` | Generación de señales (nuestra `StrategyPort`). Vectorizado; en backtest se llama una vez. |
| `custom_stake_amount(...) -> float` | Dimensionado: el risk gate aplica `max_position_quote`, exposición disponible y el factor *reduce-only* del advisor (`clamp_advisor_factor`). |
| `confirm_trade_entry(...) -> bool` | Última compuerta: el risk gate puede **vetar** (`return False`). Segunda red de seguridad sobre la de Freqtrade. |
| `order_filled(...)` | Auditoría: persistir veredicto + commit SHA en `audit_log`. |

El adaptador convierte en la frontera: Freqtrade trabaja en `float`; nuestro núcleo en `Decimal`. Ojo: en backtest los callbacks se comportan distinto (estado de `wallets` simulado), validar ahí también.

**Instalación nativa (reproducibilidad):**

- **macOS (ARM64)**: `brew install ta-lib`, luego `uv sync --extra freqtrade` exportando antes `TA_INCLUDE_PATH=/opt/homebrew/opt/ta-lib/include`, `TA_LIBRARY_PATH=/opt/homebrew/opt/ta-lib/lib` y `PKG_CONFIG_PATH=/opt/homebrew/opt/ta-lib/lib/pkgconfig` para que compile el wrapper `ta-lib`.
- **Windows**: instalar las VC++ Build Tools ("Desktop development with C++") antes de `uv sync --extra freqtrade`.
- El venv lo resuelve `uv` con Python 3.12 (cumple `requires-python` y es el más rodado con Freqtrade).

**Backtest de humo** (Fase 1): `freqtrade download-data --userdir user_data --config user_data/config.json --pairs BTC/USDT ETH/USDT --timeframes 1h --timerange <rango>` y luego `freqtrade backtesting ... --strategy OlibuguardStrategy`. La estrategia EMA20/50 es solo el esqueleto: el objetivo es validar la infraestructura, no el retorno.

---

## 4. Arquitectura propuesta

Estilo hexagonal (puertos y adaptadores) para que el núcleo de decisión sea testeable en aislamiento y los componentes externos (exchange, IA, persistencia) sean sustituibles.

```
┌─────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                            │
│  CLI / TUI / API local — start, stop, kill-switch, status        │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                      ORQUESTADOR (loop)                          │
│  tick → fetch market → strategy → risk gate → order manager      │
└──┬────────────┬─────────────┬────────────┬────────────┬─────────┘
   │            │             │            │            │
┌──▼───┐   ┌────▼────┐   ┌────▼─────┐ ┌────▼─────┐ ┌───▼──────┐
│Market│   │Strategy │   │Risk Gate │ │Order Mgr │ │AI Advisor│
│ Data │   │ Engine  │   │(guards)  │ │          │ │(opcional)│
└──┬───┘   └─────────┘   └──────────┘ └────┬─────┘ └──────────┘
   │                                       │
┌──▼─────────┐                       ┌─────▼──────┐
│Exchange    │                       │Exchange    │
│Adapter (ccxt)                      │Adapter (ccxt)
└────────────┘                       └────────────┘

┌────────────────────────────────────────────────────────────────┐
│  PERSISTENCE (SQLite local)                                    │
│  trades, orders, decisions, audit_log, market_snapshots        │
└────────────────────────────────────────────────────────────────┘
```

Componentes:

- **Market Data**: lee precios, velas, order book. Vía ccxt (websockets cuando posible, REST como fallback).
- **Strategy Engine**: produce señales (BUY/SELL/HOLD) a partir de datos. **No conoce** el broker, ni el tamaño de la cuenta, ni los límites. Pura función de datos → intención.
- **Risk Gate**: el módulo más importante del sistema. Recibe la intención de la estrategia y decide si se puede ejecutar y con qué tamaño. Puede rechazar cualquier orden. Ver sección 5.
- **Order Manager**: traduce intenciones aprobadas en órdenes reales, gestiona el ciclo de vida (open → filled / cancelled / partial), reconcilia con el estado del exchange.
- **AI Advisor (opcional)**: ver sección 7.
- **Persistencia**: ver sección 6.
- **Control Plane**: CLI mínima al principio; eventualmente TUI con `Textual` o monitor web local. Permite ver estado, detener todo, activar kill-switch.

Principio: **la estrategia no opera, propone**. La que opera es la combinación risk gate + order manager. La estrategia es reemplazable; el risk gate es invariante.

---

## 5. Guardarraíles (lo más importante)

Catálogo completo de las protecciones que tienen que estar antes de que el bot toque dinero real. Muchas las podemos heredar de Freqtrade pero todas deben estar verificadas y, donde aplique, duplicadas en nuestro propio risk gate.

**Estado de implementación (2026-05-26)** — sincronizado con el código. Leyenda: ✅ hecho · 🔶 parcial · ⏳ pendiente.

| Guardarraíl | Estado | Dónde |
|---|---|---|
| 5.1 Modos + confirmación live | ✅ | `olibuguard/modes.py`, `olibuguard/cli.py` |
| 5.2 Límites duros + sizing como % del capital | ✅ | `olibuguard/risk/gate.py`, `olibuguard/config.py` |
| 5.3 Circuit breakers (pérdida diaria, drawdown, trade rate) | 🔶 en el gate; price-sanity / exchange-disconnect / clock-skew los cubre Freqtrade | `olibuguard/risk/gate.py` |
| 5.4 Kill-switch | 🔶 runtime vía `protections` de Freqtrade (MaxDrawdown/StoplossGuard/CooldownPeriod); flag-file / CLI-stop / HTTP propios ⏳ | `user_data/strategies/OlibuguardStrategy.py` |
| 5.5 Idempotencia y reconciliación | ⏳ Fase 2 (Freqtrade persiste el estado de trades en SQLite entretanto) | — |
| 5.6 Credenciales | ✅ `keyring` + `python-dotenv` (.env), nunca en código | `olibuguard/secrets.py`, `.env.example` |
| 5.7 Auditoría (`audit_log`) | ⏳ Fase 2 | — |
| 5.8 Sanity checks (smoke + property-based) | 🔶 hecho; backtest de 1 año en CI ⏳ | `tests/`, `olibuguard/cli.py` |

Detalle por guardarraíl abajo. El sizing dinámico (`max_risk_per_trade_pct`, % del equity) y los circuit breakers (`daily_loss_limit_pct`, `max_drawdown_pct`) viven en el risk gate; el adaptador de Freqtrade los aplica en `custom_stake_amount` / `confirm_trade_entry`.

### 5.1 Modos del bot (separación dura)

Tres modos mutuamente excluyentes, configurados a nivel de proceso, no de runtime:

1. **`backtest`** — sobre datos históricos, sin conexión al exchange.
2. **`paper` (dry-run)** — conectado al exchange en modo lectura, las órdenes se simulan en memoria/SQLite y nunca se envían.
3. **`live`** — órdenes reales.

El modo se decide al arrancar el proceso, mediante variable de entorno o argumento de CLI, y queda fijado. Cambiar de modo requiere reiniciar. El binario no puede pasar de `paper` a `live` solo.

Además: **el modo `live` requiere un flag de confirmación extra**, p.ej. `--i-understand-this-is-real-money`, y el bot loggea en arranque el modo en colores chillones.

### 5.2 Límites duros (hard limits) — antes de cada orden

Todos verificados por el risk gate, todos configurables, todos con defaults conservadores:

- **Max position size per trade** (en € o en % del capital, lo que sea menor).
- **Max exposure total** (suma de posiciones abiertas).
- **Max number of open positions** simultáneamente.
- **Max orders por minuto / hora** (anti runaway).
- **Min order size** (no enviar órdenes ridículas que generen fees > P&L).
- **Max slippage tolerado** entre precio de señal y precio de ejecución.
- **Whitelist de pares** que el bot puede operar; nunca un par fuera de la lista, aunque la estrategia lo pida.
- **Blacklist de pares** explícitamente prohibidos (stablecoins raras, tokens recién listados, etc.).

### 5.3 Circuit breakers — durante la operación

Condiciones que pausan automáticamente el bot:

- **Daily loss limit**: si pierdo más de X€ o X% en un día → STOP, no abrir nuevas posiciones hasta intervención manual.
- **Drawdown desde pico**: si el equity cae más de Y% desde el máximo → STOP.
- **Trade rate anomaly**: si en N minutos se han ejecutado M órdenes (anomalía respecto a la media histórica) → STOP.
- **Price sanity check**: si el precio del par cambia >X% en N segundos → ignorar tick y/o pausar el par.
- **Exchange disconnect**: si pierdo conexión con el exchange más de N segundos → cancelar órdenes pendientes y entrar en modo "no nuevas operaciones" hasta reconectar.
- **Clock skew**: si la diferencia entre mi reloj y el del exchange supera un umbral → STOP (síntoma típico de problemas en peticiones firmadas).

### 5.4 Kill-switch

Tres formas independientes de parar el bot, en orden de drasticidad:

1. **Soft stop**: deja de abrir nuevas posiciones, mantiene las existentes hasta que sus reglas de salida disparen.
2. **Flat-and-stop**: cancela órdenes pendientes, cierra posiciones abiertas a mercado, se para.
3. **Hard kill**: termina el proceso. El siguiente arranque hará reconciliación.

Triggers del kill-switch:
- Archivo flag en disco (`./KILL_SWITCH` → si existe, hard kill al detectarlo).
- Comando CLI (`tradingbot stop --flat`).
- Endpoint local HTTP autenticado (`POST /kill` sobre 127.0.0.1).
- Atajo de teclado si hay TUI.

### 5.5 Idempotencia y reconciliación

- Cada orden lleva un `client_order_id` único determinista (UUID v4 generado por nosotros y guardado en SQLite *antes* de enviar al exchange). Si el bot crashea entre "decidí enviar" y "el exchange confirmó", al arrancar puede consultar el estado de esa ID en el exchange y saber si la orden llegó o no.
- Al arrancar, el bot ejecuta una **rutina de reconciliación**: comparar estado local (SQLite) vs estado real en el exchange. Si discrepan, alerta y entra en modo seguro hasta resolución.

### 5.6 Credenciales

- Las API keys del exchange **NO** van en archivos planos en disco. Usar el credential manager de Windows (DPAPI vía `keyring`) o un `.env` cifrado con clave derivada de passphrase pedida en arranque. *(Implementado: `olibuguard/secrets.py` usa `keyring`; además se carga `.env` con `python-dotenv` al arrancar la CLI; plantilla en `.env.example`. Para Freqtrade en live, las claves se inyectan como `FREQTRADE__EXCHANGE__KEY/SECRET`.)*
- Las API keys del exchange se crean con **permisos mínimos**: trading sí, retiro NO. Activar IP whitelist en el exchange para la IP pública de casa.
- Llave separada para entornos paper / live cuando el exchange lo permita (Binance Spot Testnet usa endpoint distinto).
- Nunca loggear claves, ni siquiera enmascaradas, ni siquiera en debug.

### 5.7 Auditoría

Cada decisión que toma el bot se persiste en una tabla `audit_log` con:

- Timestamp (con zona horaria explícita, UTC en almacenamiento).
- Snapshot de inputs (precios, indicadores, estado de cuenta).
- Decisión propuesta por la estrategia.
- Veredicto del risk gate (aprobada / rechazada + motivo).
- Resultado de ejecución (si aplica).
- Hash de la versión del código que tomó la decisión (commit SHA del bot).

Esto permite reproducir cualquier operación post-mortem. Es la base para mejorar la estrategia con datos reales.

### 5.8 Sanity checks en tiempo de desarrollo

- Tests unitarios obligatorios del módulo de riesgo. Property-based testing con `hypothesis` para los hard limits.
- Test de integración: backtest sobre 1 año de datos históricos como parte de CI local.
- Smoke test antes de cada arranque: el bot lee config, conecta al exchange, verifica que puede leer balances y velas, comprueba que `risk_gate` rechaza un par de órdenes inválidas (test interno) y solo entonces empieza a operar.

### 5.9 Realidad de ejecución (fricción de mercado)

Consejos para que lo rentable en backtest no se evapore en real. Leyenda: ✅ hecho · 🔶 parcial · ⏳ Fase 2 · 🔌 lo cubre Freqtrade.

- **Comisiones (maker/taker):** órdenes límite (maker) por defecto + `fee` explícito en `config.json`. ✅ · 🔶 pendiente umbral de "ganadora neta de fees" al bajar de timeframe.
- **Spread (bid/ask):** Freqtrade cotiza contra el order book (`use_order_book`), no contra el `close`. ✅
- **Slippage:** guard en el risk gate (`max_slippage_pct`). ✅
- **Sincronización de reloj:** `adjustForTimeDifference` activado en `ccxt_config` (peticiones firmadas). ✅ · mantener NTP en el SO es responsabilidad de despliegue. 🔶
- **WebSockets / rate limits:** los gestiona Freqtrade. 🔌
- **Reconexión sin órdenes duplicadas:** Freqtrade reconecta y persiste; reconciliación propia al arranque → Fase 2. ⏳
- **Pares líquidos:** whitelist BTC/USDT, ETH/USDT. ✅
- **Velas cerradas (sin look-ahead):** `process_only_new_candles` + cruce con `shift(1)`. ✅
- **Paper trading prolongado (2–4 semanas):** criterio de salida de la Fase 2. ⏳

---

## 6. Persistencia

**SQLite local** como almacén principal. Suficiente y sobra para volumen personal, sin servicio externo, copias = un archivo.

Tablas mínimas:

- `config_snapshot` — config completa serializada en cada arranque, para tener trazabilidad de qué parámetros estaba usando el bot.
- `market_data` — velas / ticks descargados (opcional, también se pueden recachear desde el exchange).
- `signals` — señales producidas por la estrategia, aprobadas y rechazadas.
- `orders` — órdenes enviadas, con estado.
- `trades` — operaciones completadas (par open + close).
- `audit_log` — ver 5.7.
- `equity_curve` — snapshot del valor de la cuenta cada N minutos.

Backups: `litestream` o simplemente copia del `.sqlite` cada N horas a otra ubicación (otro disco, OneDrive). El archivo es pequeño.

Esquema gestionado con `alembic` (migraciones), aunque sea para uno solo: garantiza que cambios futuros no rompan la base de datos del histórico.

---

## 7. IA opcional (Bedrock) — interfaz limpia

El requisito es que la IA sea desenchufable. La forma de hacerlo:

### Interfaz abstracta

```python
class AIAdvisor(Protocol):
    def opinion(self, context: MarketContext) -> AdvisorOpinion | None:
        """Devuelve None si el advisor no quiere opinar / está deshabilitado."""
```

`AdvisorOpinion` no es nunca una orden; es un score / sesgo (`bias`) entre -1 y +1 con un texto justificativo. La estrategia decide cuánto peso darle (puede ser 0). El risk gate no se salta nunca por que la IA "esté muy convencida".

### Implementaciones

- `NullAdvisor` — devuelve siempre `None`. **Es el default**. El bot funciona perfectamente sin IA.
- `BedrockAdvisor` — usa boto3 → bedrock-runtime. Configurable por modelo. Solo se carga si está habilitado en config.

### Habilitación

En `config.yaml`:

```yaml
ai:
  enabled: false        # default false
  provider: bedrock     # o "null"
  model: anthropic.claude-...
  weight: 0.0           # cuánto peso le da la estrategia, 0 = ignora
  region: eu-west-1
```

Si `enabled: false` o `provider: null`, el código de Bedrock ni siquiera se importa. La dependencia `boto3` es opcional (en `pyproject.toml` como `extra`).

### Salvaguarda

La IA solo puede **vetar o reducir** una operación, nunca **iniciarla o agrandarla** más allá de lo que la estrategia propone. Esto es un guardarraíl explícito: aunque algún día el LLM diga "compra todo lo que tengas", el risk gate y el cap de la estrategia lo ignoran.

---

## 8. Estrategia para empezar

Como no tienes decidida la estrategia, mi orientación: **no empieces tratando de predecir el precio**. Eso es la trampa donde caen 90% de los bots personales y donde la varianza del PnL es tan grande que no puedes saber si tu bot es bueno o tuvo suerte.

Empieza con una estrategia conocida, simple, auditable y bien estudiada, que sirva como esqueleto del sistema. Tres candidatas razonables para fase 1:

1. **Mean reversion con Bollinger Bands + RSI** sobre pares con buena liquidez (BTC/USDT, ETH/USDT). Compra cuando precio < banda inferior y RSI < 30; vende cuando vuelve a la media o RSI > 70. Comportamiento intuitivo, fácil de razonar.

2. **Cruce de medias móviles (EMA 20 / EMA 50)** en timeframe 1h o 4h. Comportamiento de seguimiento de tendencia. Largos periodos sin operación, lo cual es bueno para validar que el bot está cuerdo.

3. **DCA + take-profit dinámico** (Dollar Cost Averaging). El bot compra cantidades pequeñas en caídas pre-definidas y vende cuando alcanza un margen de beneficio. La menos "trading" de las tres, la más "ahorro programado".

Mi sugerencia: empieza por la (2) o la (3). Son aburridas, que es exactamente lo que queremos en fase 1. El objetivo no es maximizar return, es validar que la infraestructura funciona durante meses sin sorpresas.

Cuando la infra esté estable, la estrategia se vuelve un parámetro: probar variantes en backtest, hyperopt, y solo promover a paper trading aquellas que pasan filtros estadísticos (Sharpe, max drawdown, número de operaciones suficiente para no ser ruido).

---

## 9. Roadmap por fases

Orden estricto. No se avanza de fase hasta cumplir los criterios de salida.

### Fase 0 — Setup (días)
- Repo Git, Python 3.12, uv/poetry, ruff, mypy, pytest, pre-commit.
- Estructura de carpetas hexagonal vacía con interfaces definidas.
- Config con pydantic, logging estructurado, manejo de secretos vía keyring.
- Smoke test que arranca el proceso, lee config y termina limpio.

### Fase 1 — Backtest end-to-end (1-2 semanas)
- Adaptador de market data que lee histórico (CSV o vía ccxt fetch_ohlcv).
- Una estrategia trivial (EMA crossover) implementada contra la interfaz.
- Engine de backtest (o usar el de Freqtrade) corriendo y produciendo equity curve.
- Tests unitarios del módulo de riesgo con hipotetic property-based.
- **Criterio de salida**: puedo correr un backtest reproducible y ver gráfico de P&L.

### Fase 2 — Paper trading (semanas)
- Conexión al exchange (testnet de Binance primero) en modo dry-run.
- Risk gate completo (sección 5.2 y 5.3) implementado y testeado.
- Persistencia completa en SQLite con audit log.
- Kill-switch funcionando (las tres formas).
- Reconciliación al arranque.
- **Criterio de salida**: el bot lleva al menos 4 semanas en paper trading sin crashes, sin órdenes "raras", la equity curve es coherente con el backtest, y todos los kill-switches han sido probados manualmente.

### Fase 3 — IA opcional (opcional, en paralelo)
- Interfaz `AIAdvisor` + `NullAdvisor`.
- `BedrockAdvisor` detrás de feature flag.
- Tests que confirman que con `enabled: false` no se importa boto3.
- Validar en backtest que añadir el advisor con peso bajo no degrada el sistema antes de subir el peso.

### Fase 4 — Live con cap mínimo (cuando se sienta listo, no antes)
- Capital muy pequeño (cantidades simbólicas).
- Límites duros configurados al mínimo posible.
- Monitorización activa las primeras semanas.
- Criterios pre-definidos para escalar o cancelar.

---

## 10. Decisiones abiertas / preguntas pendientes

Cosas que no hay que decidir hoy pero conviene anotar:

- **Exchange concreto para paper trading**: Binance Spot Testnet es lo más usado. Alternativa: Kraken (no tiene testnet propio pero el modo dry-run de Freqtrade simula sobre datos reales).
- ~~**¿Realmente queremos partir de Freqtrade o construir desde cero?**~~ **DECIDIDO (2026-05-26): Camino A (Freqtrade).** Ver sección 3.
- ~~**¿Instalar/correr Freqtrade nativo o en Docker?**~~ **DECIDIDO (2026-05-26): nativo en el venv `uv`** (`freqtrade` como extra `[freqtrade]`). Se acepta el coste de TA-Lib de sistema y el soporte no oficial en mac ARM64 a cambio de transparencia/auditabilidad. El núcleo `olibuguard.*` sigue sin depender de Freqtrade.
- **Timeframe principal**: 1h, 4h, diario. Cuanto más alto, menos sensible a microestructura y mejor para empezar.
- **Pares concretos del whitelist inicial**: probablemente BTC/USDT y ETH/USDT, suficiente liquidez y datos.
- **¿Cuenta dedicada en el exchange?** Recomiendo separar una cuenta solo para el bot, no usar la cuenta personal donde haya HODL.
- **Política de fiscalidad**: si llega el día de operar real, anotar que en España cada operación cuenta como transmisión patrimonial. El audit log servirá para reportar.

---

## 11. Resumen ejecutivo

- **Python con tipado estricto**, no Go.
- **Empezar sobre Freqtrade** y añadir nuestras capas (estrategia, risk gate propio, AI opcional, auditoría).
- **Tres modos separados**: backtest, paper, live. Cambio explícito y consciente.
- **Risk gate como módulo invariante**. La estrategia propone, el risk gate dispone.
- **Catálogo de guardarraíles desde el día uno**: límites duros, circuit breakers, kill-switches múltiples, idempotencia, reconciliación, auditoría completa.
- **SQLite local** para persistencia.
- **IA detrás de interfaz opcional**, default `NullAdvisor`. Bedrock solo si quiero. Nunca puede agrandar una operación.
- **Roadmap en fases con criterios de salida estrictos**. Live solo después de meses de paper trading limpio.
