from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from olibuguard.config import AppConfig, load_config


def test_defaults_are_conservative() -> None:
    cfg = AppConfig()
    assert cfg.ai.enabled is False
    assert cfg.ai.provider == "null"
    assert cfg.exchange.testnet is True
    assert "BTC/USDT" in cfg.risk.whitelist


def test_loads_example_yaml() -> None:
    path = Path(__file__).resolve().parents[1] / "config.example.yaml"
    cfg = load_config(path)
    assert cfg.risk.max_position_quote == Decimal("50")
    assert cfg.ai.provider == "null"
    assert cfg.exchange.name == "binance"
