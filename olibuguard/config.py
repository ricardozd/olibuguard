"""Configuración validada con pydantic. Defaults conservadores."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RiskLimits(BaseModel):
    """Límites duros y circuit breakers (secciones 5.2/5.3). El risk gate los aplica
    antes de cada orden. Todos los porcentajes son fracciones (0.02 = 2%)."""

    model_config = ConfigDict(extra="forbid")

    # Sizing dinámico: máximo riesgo por operación como fracción del capital real.
    max_risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    # Caps absolutos (cinturón y tirantes sobre el sizing dinámico).
    max_position_quote: Decimal = Field(default=Decimal("50"), gt=0)
    max_total_exposure_quote: Decimal = Field(default=Decimal("200"), gt=0)
    max_open_positions: int = Field(default=3, ge=0)
    min_order_quote: Decimal = Field(default=Decimal("10"), gt=0)  # mínimo nocional
    max_orders_per_minute: int = Field(default=6, ge=0)
    max_slippage_pct: float = Field(default=0.005, ge=0)  # fracción: 0.005 = 0.5%
    # Circuit breakers (kill-switch automático).
    daily_loss_limit_pct: float = Field(default=0.05, gt=0, le=1)
    max_drawdown_pct: float = Field(default=0.10, gt=0, le=1)
    whitelist: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    blacklist: list[str] = Field(default_factory=list)


class AIConfig(BaseModel):
    """IA opcional (sección 7). Default deshabilitada; boto3 no se importa si no."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: Literal["null", "bedrock"] = "null"
    model: str = ""
    weight: float = Field(default=0.0, ge=0.0, le=1.0)
    region: str = "eu-west-1"


class ExchangeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "binance"
    testnet: bool = True
    quote_currency: str = "USDT"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    ai: AIConfig = Field(default_factory=AIConfig)
    timeframe: str = "1h"


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return AppConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"config en {path} no es un mapping YAML válido")
    return AppConfig.model_validate(raw)
