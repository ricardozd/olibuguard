# Olibuguard — Operaciones

## Arrancar el bot (Docker)

```bash
# 1. Autenticarse en AWS (sesión válida ~12 h, necesaria para el AI advisor)
aws login

# 2. Exportar credenciales STS al .env
task aws-refresh

# 3. Build + arrancar como daemon
task docker-up

# 4. Sacar del estado STOPPED (Freqtrade arranca parado por seguridad)
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

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
docker compose restart olibuguard
curl -s -X POST http://localhost:8081/api/v1/start -u olibuguard:rioli1010!
```

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

# Paper trading nativo (sin Docker)
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
