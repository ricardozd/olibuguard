# Olibuguard — Arquitectura

## Principio de diseño

**Arquitectura hexagonal**: el core de olibuguard (`olibuguard.*`) no conoce Freqtrade.
Toda la lógica de negocio vive en el core; Freqtrade es solo un adaptador de entrada/salida.

```
┌─────────────────────────────────────────────┐
│              Freqtrade (framework)           │
│                                             │
│   OlibuguardStrategy (adaptador)            │
│   ├── populate_indicators   (señales)       │
│   ├── custom_stake_amount   (sizing)        │
│   └── confirm_trade_entry   (decisión)      │
│                    │                        │
└────────────────────┼────────────────────────┘
                     │ domain types
                     ▼
┌─────────────────────────────────────────────┐
│              olibuguard core                │
│                                             │
│   RiskGate          risk/gate.py            │
│   AIAdvisor         advisor/bedrock.py      │
│   KillSwitch        kill_switch.py          │
│   AuditSink         audit/sqlite.py         │
│   AlertSink         alerts/telegram.py      │
│   Reconciliation    reconciliation.py       │
└─────────────────────────────────────────────┘
```

---

## Módulos del core

### `domain/`
Tipos puros sin dependencias externas.
- `models.py` — `OrderIntent`, `PortfolioState`, `RiskVerdict`, `MarketContext`, `Side`
- `ports.py` — Protocolos (interfaces) para `AIAdvisor`, `AuditSink`, `AlertSink`

### `risk/gate.py` — RiskGate
El módulo invariante. Evalúa una `OrderIntent` contra el `PortfolioState` y devuelve un `RiskVerdict`.
Puede rechazar o reducir el tamaño, nunca ampliar.
Ver [overview.md](overview.md) para el orden de evaluación.

### `advisor/`
- `base.py` — `AIAdvisor` Protocol + `NullAdvisor` (no-op, fail-safe default)
- `bedrock.py` — `BedrockAdvisor`: llama a Claude vía AWS Bedrock. Cualquier error → `None` (abstención).

### `audit/`
- `records.py` — `DecisionAudit`, `EquityPoint` (dataclasses)
- `sink.py` — `AuditSink` Protocol + `NullAuditSink`
- `sqlite.py` — `SQLiteAuditSink`: persistencia real
- `version.py` — hash del código en ejecución para el audit trail

### `alerts/`
- `sink.py` — `AlertSink` Protocol + `NullAlertSink`
- `telegram.py` — `TelegramAlertSink`: envía mensajes al bot de Telegram

### `kill_switch.py`
Fichero centinela en disco. `is_active()` = el fichero existe.
`task kill` / `task resume` lo crean/borran.

### `reconciliation.py`
- `restore_peak_equity()` — lee el equity pico de la BD al arrancar
- `check_equity_drift()` — detecta desincronía > 5% entre la BD y el exchange

### `failsafe.py`
- `run_safe(label, fn, default)` — ejecuta `fn`, captura cualquier excepción, devuelve `default`
- `ErrorBudget` — contador de fallos consecutivos; tras N fallos activa el kill switch

### `config.py`
Modelos Pydantic para `AppConfig`, `RiskLimits`, `AIConfig`. Validación estricta (`extra = "forbid"`).

---

## Flujo de datos por vela

```
Nueva vela 1h
    │
    ▼
populate_indicators()
    Calcula EMA 20, EMA 50, RSI 14, volume_ratio
    Genera señal: ema_cross_up = 1 si EMA20 > EMA50
    │
    ▼
populate_entry_trend()
    enter_long = ema_cross_up
    │
    ▼  (si hay señal)
custom_stake_amount()
    RiskGate captura el tamaño máximo permitido
    │
    ▼
confirm_trade_entry()
    1. ¿Kill switch activo? → False
    2. AI Advisor → ¿veto? → False
    3. RiskGate.evaluate() → ¿aprobado? → True/False
    4. Audit: registra la decisión
```

---

## Principios de seguridad

1. **Fail-safe**: cualquier componente opcional (AI, audit, alerts) que falle → el bot continúa.
2. **Veto-only AI**: la IA solo puede rechazar, nunca iniciar ni ampliar.
3. **Doble circuit breaker**: olibuguard (código) + Freqtrade (protections) como segunda red.
4. **Sin claves en disco**: credenciales del exchange vía env vars; credenciales AWS via STS temporal.
5. **Audit inmutable**: SQLite append-only, nunca se sobreescribe un registro.
6. **Kill switch instantáneo**: fichero en disco, se comprueba antes de cada entrada.
