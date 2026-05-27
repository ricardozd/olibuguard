# Olibuguard — Roadmap

**Objetivo final**: generar **300–500 USD/mes sostenidos** para la familia.

---

## Por qué 300–500 USD/mes es alcanzable (pero requiere trabajo)

| Capital real | Retorno mensual necesario | Realismo |
|---|---|---|
| $5 000 | 6–10 % | Muy agresivo, riesgo alto |
| $10 000 | 3–5 % | Agresivo pero posible en crypto |
| $15 000 | 2–3 % | Moderado, más sostenible |

El bot en su estado actual tiene techo de $200 de exposición → genera céntimos reales.
**El camino es: validar el edge primero, luego escalar el capital.**

---

## Estado de las fases completadas

| Fase | Descripción | Estado |
|------|-------------|--------|
| **0 – Setup** | Esqueleto hexagonal, tooling, smoke tests | ✅ |
| **1 – Estrategia** | EMA 20/50 en 5m, backtest reproducible | ✅ |
| **2 – Safety layer** | Circuit breakers, audit DB, kill-switch, error budget, Telegram | ✅ |
| **3 – AI Advisor** | Claude Opus 4 via Bedrock, veto-only, extended thinking | ✅ |
| **4 – Live** | Binance real, ATR stoploss, stoploss_on_exchange | 🟡 en curso |

---

## Fases pendientes hacia el objetivo

---

### Fase 4 — Live validation (ahora)

**Duración estimada**: 4–8 semanas

**Qué hay que hacer**:
- [ ] Fondear Binance con capital mínimo real ($100–300 USDT)
- [ ] Correr `task docker-up` con órdenes reales durante 30 días
- [ ] Verificar que el safety layer funciona en producción (circuit breakers, stoploss en Binance)
- [ ] Confirmar que el AI advisor no veta todo (o si lo hace, entender por qué)
- [ ] Comparar P&L real vs paper: si difieren mucho, hay slippage o ejecución que revisar

**Criterio de éxito**: bot vivo 30 días, drawdown < 10%, sin catástrofes. No importa si gana poco.

---

### Fase 5 — Medir y mejorar la estrategia

**Duración estimada**: 4–8 semanas tras Fase 4

**Métricas objetivo**:
| Métrica | Umbral mínimo | Objetivo |
|---|---|---|
| Win rate | > 45 % | > 55 % |
| Profit factor | > 1.2 | > 1.5 |
| Sharpe ratio | > 0.8 | > 1.2 |
| Max drawdown (backtest) | < 15 % | < 10 % |
| Trades/mes | > 20 | 40–80 |

**Qué hay que hacer**:
- [ ] Backtest con 12 meses de datos en 5m (BTC/USDT + ETH/USDT)
- [ ] Hyperopt: optimizar periodos EMA, multiplicador ATR, ROI mínimo
- [ ] Añadir filtro RSI en entrada (evitar compras en sobrecompra > 70)
- [ ] Evaluar añadir SOL/USDT y BNB/USDT para más frecuencia de señales
- [ ] Evaluar añadir confirmación de volumen: no entrar si volumen < media 20v
- [ ] Comparar backtest antes/después de los cambios; solo mergear si mejora

**Criterio de éxito**: Sharpe > 1.0 en backtest, profit factor > 1.4.

---

### Fase 6 — Escalar capital gradualmente

**Duración estimada**: 3–6 meses tras Fase 5

**Plan de escalado** (nunca subir si hay pérdidas en el tramo anterior):
| Tramo | Exposición máx. | Capital necesario (approx.) | P&L mensual esperado |
|---|---|---|---|
| 4 (hoy) | $200 | — | < $10 |
| 5 | $500 | $1 500 | ~$15–25 |
| 6 | $1 500 | $5 000 | ~$50–100 |
| 7 | $4 000 | $12 000 | ~$150–300 |
| 8 | $8 000 | $20 000 | ~$300–500 ✅ |

**Reglas de escalado**:
- Solo subir un tramo si el tramo actual lleva **30 días verde** (P&L positivo)
- Nunca subir si el drawdown del mes supera 5%
- Actualizar `max_position_quote` y `max_total_exposure_quote` en `config.yaml`
- No cambiar la estrategia y escalar al mismo tiempo

**Qué hay que hacer**:
- [ ] Abrir cuenta en Binance con nivel de verificación adecuado para mover capital
- [ ] Configurar alertas Telegram para P&L diario y drawdown
- [ ] Revisar config.yaml en cada tramo (risk limits)
- [ ] Mantener un diario mensual de P&L real (ver abajo)

---

### Fase 7 — Estrategias múltiples / resiliencia a regímenes de mercado

**Duración estimada**: paralelo a Fase 6

**Problema actual**: EMA crossover solo funciona bien en mercados tendenciales. En mercados laterales (ranging), genera señales falsas y pierde dinero.

**Qué hay que hacer**:
- [ ] Detectar régimen de mercado: añadir ADX o Bollinger Band Width como filtro
  - Si ADX < 20 (mercado plano) → no operar con EMA crossover
- [ ] Evaluar añadir una segunda estrategia mean-reversion para laterales
- [ ] O simplemente: no operar cuando el mercado está plano (reduce frecuencia, mejora calidad)

---

### Fase 8 — Objetivo alcanzado

**Criterio**: 3 meses consecutivos con $300–500 USD de ganancia neta real.

**Qué hay que mantener**:
- [ ] Renovar AWS tokens cada 12h (o automatizarlo con cron)
- [ ] Revisar backtest trimestralmente (el mercado cambia)
- [ ] Monitoreo activo de circuit breakers y audit log
- [ ] Capital reserve: nunca meter en el bot más del 30% de ahorros totales

---

## Diario de P&L mensual

| Mes | Capital | P&L $ | P&L % | Drawdown máx. | Notas |
|-----|---------|--------|--------|----------------|-------|
| 2026-06 | paper | — | — | — | validando live |
| ... | | | | | |

---

## Notas de decisiones pasadas

- **2026-05**: ATR stoploss dinámico + stoploss_on_exchange activado
- **2026-05**: Timeframe cambiado 1h → 5m para más señales
- **2026-05**: AI Advisor (Claude Opus 4 + extended thinking) veto-only activo
- **2026-05**: Dual mode paper/live vía `FREQTRADE__DRY_RUN`
