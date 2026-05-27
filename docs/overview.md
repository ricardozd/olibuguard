# Olibuguard — Visión general

## Qué es

Bot de trading automático de criptomonedas sobre Binance.
Construido como proyecto de aprendizaje sobre **Freqtrade** + arquitectura hexagonal en Python.

Opera 24/7 en Docker. Soporta dos modos:
- **Paper mode** (`task paper-up`): dry-run, sin órdenes reales, ideal para validar en vivo.
- **Live mode** (`task docker-up`): órdenes reales en Binance con capital mínimo.

---

## Pares y timeframe

| Parámetro | Valor |
|---|---|
| Pares | BTC/USDT, ETH/USDT |
| Timeframe | 5 m (velas de 5 minutos) |
| Exchange | Binance |
| Moneda base | USDT |

---

## Señal de entrada

La estrategia usa un **cruce de EMAs** en la vela de 5 m:

1. **EMA 20** (rápida) cruza **por encima** de **EMA 50** (lenta) → señal de compra.
2. La estrategia también calcula:
   - **RSI 14** (Wilder EWM, sin TA-Lib) — para contexto del advisor.
   - **Volume ratio** — volumen actual / media 20 velas — para contexto del advisor.
   - **ATR 14** (Wilder EWM, sin TA-Lib) — para calcular el stoploss dinámico.

La señal de salida es el cruce inverso (EMA 20 cae por debajo de EMA 50), complementada por `minimal_roi` (10%) y el stoploss dinámico.

---

## Sizing (tamaño de posición)

El tamaño se calcula en dos pasos:

1. **Freqtrade** calcula el stake basado en `stake_amount = 50 USDT`.
2. **RiskGate** puede reducirlo (nunca ampliarlo):
   - Máx. 2% del equity real por trade.
   - Máx. 50 USDT absoluto por posición.
   - Máx. 200 USDT de exposición total abierta.

---

## Flujo de decisión por cada señal de compra

```
Señal EMA → confirm_trade_entry()
    │
    ├── Kill switch activo? → RECHAZA
    │
    ├── AI Advisor (Bedrock / Claude Opus 4)
    │       Analiza: precio, EMAs, RSI, volumen, drawdown, P&L diario
    │       → veto? → RECHAZA  (fail-safe: si falla, pasa)
    │
    └── RiskGate.evaluate()
            1. Circuit breakers activos? → RECHAZA
            2. Par en blacklist? → RECHAZA
            3. Par en whitelist? (si está definida) → RECHAZA si no está
            4. Rate limit órdenes/minuto → RECHAZA
            5. Slippage excesivo? → RECHAZA
            6. Sizing: % capital + caps absolutos
            7. Notional mínimo (10 USDT) → RECHAZA si queda por debajo
            → APRUEBA (o con tamaño reducido)
```

---

## Stoploss

El bot usa **doble stoploss** para máxima protección:

| Capa | Mecanismo | Detalle |
|---|---|---|
| **ATR dinámico** | `custom_stoploss` de Freqtrade | `stop = precio_entrada − ATR×2`. Se adapta a la volatilidad del momento. |
| **Suelo fijo** | Clase `stoploss = -0.10` | Freqtrade fuerza este límite superior: el stoploss nunca puede superar el −10%. |
| **Orden en Binance** | `stoploss_on_exchange: true` | Freqtrade envía una orden stop directamente al exchange. Si el bot se cae, Binance ejecuta el stop igualmente. |

El ATR se calcula con la fórmula de Wilder (EWM con `com=13`, panda-nativo, sin TA-Lib).

---

## Circuit breakers (parada automática)

El bot se detiene automáticamente (sin intervención humana) si:

| Condición | Umbral |
|---|---|
| Pérdida diaria | ≥ 5% del equity |
| Drawdown desde el pico | ≥ 10% del equity pico |

Además, Freqtrade tiene sus propias **protections**:
- `CooldownPeriod`: 2 velas de espera tras cerrar una posición.
- `MaxDrawdown`: para si el drawdown supera 10% en 48 velas.
- `StoplossGuard`: para si hay ≥ 4 stop-losses en 24 velas.

---

## AI Advisor (AWS Bedrock / Claude Opus 4)

- Solo puede **vetar** una operación, nunca iniciarla ni ampliarla.
- Recibe contexto completo: precio, EMAs, RSI, volumen, últimos 5 cierres, equity, drawdown, P&L diario.
- Usa **extended thinking** (5000 tokens de razonamiento interno antes de decidir).
- Si Bedrock falla o no está disponible → `NullAdvisor` → la operación continúa (fail-safe).

---

## Audit trail

Cada decisión se persiste en SQLite (`user_data/olibuguard_audit.sqlite`):

- **DecisionAudit**: qué pair, qué precio, qué decidió el risk gate y por qué.
- **EquityPoint**: snapshot del equity cada 5 minutos como máximo.

Al arrancar, el bot restaura el **equity pico** desde la BD para que el circuit breaker de drawdown no se resetee tras un reinicio.

---

## Alertas Telegram

Notificaciones automáticas para:
- Arranque del bot.
- Activación del kill switch.
- Equity drift > 5% intra-candle (posible desincronía con el exchange).

---

## Kill switch manual

Para detener entradas nuevas de forma inmediata (sin tocar posiciones abiertas):

```bash
task kill        # activa el kill switch (crea fichero KILL_SWITCH)
task resume      # desactiva el kill switch
```

El fichero `user_data/KILL_SWITCH` es la señal. El bot lo comprueba antes de cada entrada.
