"""Control plane (CLI).

Comandos:
  smoke  Arranca, lee config, auto-chequea el risk gate y termina limpio.
  run    Arranca el bot en un modo concreto (backtest | paper | live).

Guardarraíl (5.1): el modo se fija al arrancar; ``live`` exige confirmación
explícita con ``--i-understand-this-is-real-money`` y banner en rojo.
"""

from __future__ import annotations

import argparse
import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from olibuguard import __version__
from olibuguard.advisor.base import NullAdvisor
from olibuguard.config import AppConfig, load_config
from olibuguard.domain.models import OrderIntent, PortfolioState, Side
from olibuguard.logging import configure_logging, get_logger
from olibuguard.modes import Mode
from olibuguard.risk.gate import RiskGate

_console = Console()
_LIVE_FLAG = "--i-understand-this-is-real-money"


def _load(config_path: str | None) -> AppConfig:
    if config_path is None:
        return AppConfig()
    return load_config(Path(config_path))


def _resolve_mode(raw: str | None) -> Mode | None:
    value = raw or os.environ.get("OLIBUGUARD_MODE")
    if value is None:
        return None
    try:
        return Mode(value.lower())
    except ValueError:
        return None


def _banner(mode: Mode) -> None:
    styles: dict[Mode, tuple[str, str]] = {
        Mode.BACKTEST: ("BACKTEST", "bold cyan"),
        Mode.PAPER: ("PAPER (dry-run)", "bold green"),
        Mode.LIVE: ("LIVE — DINERO REAL", "bold white on red"),
    }
    label, style = styles[mode]
    _console.print(Panel(f"MODO: {label}", style=style, expand=False))


def _cmd_smoke(args: argparse.Namespace) -> int:
    configure_logging(json_logs=False)
    log = get_logger("smoke")
    config = _load(args.config)
    log.info("config.loaded", whitelist=config.risk.whitelist)

    gate = RiskGate(config.risk)
    NullAdvisor()  # default advisor: nunca opina
    state = PortfolioState()
    allowed = config.risk.whitelist[0] if config.risk.whitelist else "BTC/USDT"

    # El risk gate DEBE rechazar órdenes inválidas (auto-chequeo).
    out_of_whitelist = OrderIntent(
        symbol="FOO/BAR",
        side=Side.BUY,
        quote_amount=config.risk.min_order_quote,
        reference_price=Decimal("1"),
    )
    too_small = OrderIntent(
        symbol=allowed,
        side=Side.BUY,
        quote_amount=config.risk.min_order_quote / 2,
        reference_price=Decimal("1"),
    )
    for check, intent in (("whitelist", out_of_whitelist), ("min_order", too_small)):
        if gate.evaluate(intent, state).approved:
            log.error("smoke.fail", check=check)
            _console.print(f"[bold red]smoke FAIL[/]: risk gate aprobó orden inválida ({check})")
            return 1

    log.info("smoke.ok")
    _console.print("[bold green]smoke OK[/]")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    mode = _resolve_mode(args.mode)
    if mode is None:
        _console.print(
            "[bold red]error:[/] indica --mode {backtest,paper,live} (o OLIBUGUARD_MODE)"
        )
        return 2
    if mode.touches_real_money and not args.i_understand_this_is_real_money:
        _console.print(
            f"[bold red]error:[/] el modo LIVE opera con DINERO REAL. "
            f"Re-ejecuta con {_LIVE_FLAG} si de verdad es lo que quieres."
        )
        return 2

    configure_logging(json_logs=True)
    log = get_logger("run")
    _banner(mode)
    config = _load(args.config)
    log.info(
        "startup",
        mode=mode.value,
        exchange=config.exchange.name,
        testnet=config.exchange.testnet,
    )
    log.warning(
        "loop.not_implemented",
        phase="0",
        detail="market data y order manager llegan en Fase 1-2; nada que operar todavía",
    )
    _console.print("[yellow]Fase 0: sin loop de trading todavía. Saliendo limpio.[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # carga .env si existe (secretos fuera del código; ver .env.example)
    parser = argparse.ArgumentParser(
        prog="olibuguard",
        description="Bot de trading cripto (guardarraíles primero).",
    )
    parser.add_argument("--version", action="version", version=f"olibuguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_smoke = sub.add_parser(
        "smoke", help="Arranca, lee config, auto-chequea el risk gate y termina."
    )
    p_smoke.add_argument("--config", default=None, help="Ruta a config.yaml (opcional).")

    p_run = sub.add_parser("run", help="Arranca el bot en un modo concreto.")
    p_run.add_argument("--mode", default=None, help="backtest | paper | live")
    p_run.add_argument("--config", default=None, help="Ruta a config.yaml (opcional).")
    p_run.add_argument(
        _LIVE_FLAG,
        dest="i_understand_this_is_real_money",
        action="store_true",
        help="Confirmación obligatoria para operar en LIVE con dinero real.",
    )

    args = parser.parse_args(argv)
    if args.command == "smoke":
        return _cmd_smoke(args)
    if args.command == "run":
        return _cmd_run(args)
    return 2
