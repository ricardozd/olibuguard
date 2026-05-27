# Olibuguard — Operaciones

## Arrancar el bot (Docker)

### Paper mode (sin dinero real)

```bash
# 1. Credenciales AWS para el AI advisor (tokens STS, válidos ~12 h)
aws login
task aws-refresh

# 2. Build + arrancar en paper mode
task paper-up

# 3. Sacar del estado STOPPED
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

### Live mode (órdenes reales en Binance)

```bash
# 1. Credenciales AWS
aws login
task aws-refresh

# 2. Build + arrancar en live mode
task docker-up

# 3. Sacar del estado STOPPED
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

La diferencia entre ambos modos es únicamente la variable `FREQTRADE__DRY_RUN` que inyecta
el task. La imagen Docker y la estrategia son idénticas.

El bot queda corriendo 24/7 gracias a `restart: unless-stopped` en docker-compose.

---

## Panel web (FreqUI)

Accesible en **http://localhost:8081** (solo desde localhost, nunca desde la red).

Credenciales: `olibuguard` / `rioli1010!`

---

## Logs en tiempo real

```bash
task docker-logs
```

---

## Parar el bot

```bash
task docker-down
```

---

## Renovar credenciales AWS (cada ~12 h)

Las credenciales STS expiran. Para renovarlas sin parar el bot:

```bash
aws login           # si la sesión del host también expiró
task aws-refresh    # escribe nuevos tokens en .env

# IMPORTANTE: restart no aplica cambios del compose file; usar down+up
docker compose down
task paper-up       # o task docker-up según el modo
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

> `docker compose restart` **no** recarga las variables de entorno del compose file.
> Siempre usar `down` + `paper-up` / `docker-up`.

---

## Kill switch de emergencia

Detiene **nuevas entradas** inmediatamente, sin cerrar posiciones abiertas:

```bash
task kill      # activa
task resume    # desactiva
```

---

## Desarrollo local (sin Docker)

```bash
# Instalar dependencias
task install

# Tests, lint, typecheck
task check

# Paper trading nativo (sin Docker, mercado real, sin órdenes reales)
task paper
```

---

## Backtesting

```bash
# Descargar datos históricos (ejemplo: Q1 2025)
task download -- --timerange 20250101-20250401

# Ejecutar backtest
task backtest -- --timerange 20250101-20250401
```

---

## Consultar el audit trail

```bash
# Últimas 20 decisiones del risk gate
sqlite3 user_data/olibuguard_audit.sqlite \
  "SELECT at, symbol, approved, reason FROM audit_log ORDER BY at DESC LIMIT 20;"

# Últimas 10 snapshots de equity
sqlite3 user_data/olibuguard_audit.sqlite \
  "SELECT at, equity_quote FROM equity_curve ORDER BY at DESC LIMIT 10;"
```

---

## Archivos clave

| Archivo | Qué es |
|---|---|
| `user_data/config.json` | Configuración de Freqtrade (exchange, pares, ROI, stoploss) |
| `user_data/config.yaml` | Configuración de olibuguard (risk limits, AI advisor) |
| `.env` | Secretos y credenciales (NUNCA commitear) |
| `user_data/strategies/OlibuguardStrategy.py` | Adaptador Freqtrade → core olibuguard |
| `user_data/olibuguard_audit.sqlite` | Audit trail de decisiones y equity |
| `user_data/KILL_SWITCH` | Fichero centinela del kill switch (existe = activado) |

---

## Variables de entorno relevantes

| Variable | Propósito |
|---|---|
| `OLIBUGUARD_CONFIG` | Ruta al `config.yaml` |
| `FREQTRADE__EXCHANGE__KEY/SECRET` | API keys del exchange |
| `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN` | Credenciales STS para Bedrock (via `task aws-refresh`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alertas Telegram |
