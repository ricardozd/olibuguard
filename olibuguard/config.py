"""Configuration validated with pydantic. Conservative defaults."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskLimits(BaseModel):
    """Hard limits and circuit breakers (sections 5.2/5.3). The risk gate applies
    them before every order. All percentages are fractions (0.02 = 2%)."""

    model_config = ConfigDict(extra="forbid")

    # Dynamic sizing: max risk per trade as a fraction of real capital.
    max_risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    # Absolute caps (belt and suspenders on top of dynamic sizing).
    max_position_quote: Decimal = Field(default=Decimal("50"), gt=0)
    max_total_exposure_quote: Decimal = Field(default=Decimal("200"), gt=0)
    max_open_positions: int = Field(default=3, ge=0)
    min_order_quote: Decimal = Field(default=Decimal("10"), gt=0)  # min notional
    max_orders_per_minute: int = Field(default=6, ge=0)
    max_slippage_pct: float = Field(default=0.005, ge=0)  # fraction: 0.005 = 0.5%
    # Circuit breakers (automatic kill-switch).
    daily_loss_limit_pct: float = Field(default=0.05, gt=0, le=1)
    max_drawdown_pct: float = Field(default=0.10, gt=0, le=1)
    whitelist: list[str] = Field(default_factory=list)  # empty = allow all pairs (fail-safe)
    blacklist: list[str] = Field(default_factory=list)


class AIConfig(BaseModel):
    """Optional AI (section 7). Disabled by default; boto3 is not imported if off."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: Literal["null", "bedrock"] = "null"
    # Cross-region inference profile (eu.anthropic.*) or base model ARN.
    model: str = "eu.anthropic.claude-opus-4-7"
    weight: float = Field(default=0.0, ge=0.0, le=1.0)
    region: str = "eu-west-1"
    # Named AWS profile from ~/.aws/config (role-based auth, no static keys).
    # Leave None to let boto3 use the default credential chain.
    profile: str | None = None
    max_tokens: int = Field(default=8192, gt=0)
    # Extended thinking: Claude reasons step-by-step before producing the verdict.
    # budget_tokens must be strictly less than max_tokens.
    thinking: bool = False
    thinking_budget_tokens: int = Field(default=5000, gt=0)
    timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _thinking_budget_fits(self) -> AIConfig:
        if self.thinking and self.thinking_budget_tokens >= self.max_tokens:
            raise ValueError(
                f"thinking_budget_tokens ({self.thinking_budget_tokens}) "
                f"must be less than max_tokens ({self.max_tokens})"
            )
        return self


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
        raise ValueError(f"config at {path} is not a valid YAML mapping")
    return AppConfig.model_validate(raw)
