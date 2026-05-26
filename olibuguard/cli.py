"""Control plane (CLI).

Commands:
  smoke  Start, read config, self-check the risk gate and exit cleanly.
  run    Start the bot in a specific mode (backtest | paper | live).

Guardrail (5.1): the mode is fixed at startup; ``live`` requires explicit
confirmation via ``--i-understand-this-is-real-money`` and a red banner.
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
        Mode.LIVE: ("LIVE — REAL MONEY", "bold white on red"),
    }
    label, style = styles[mode]
    _console.print(Panel(f"MODE: {label}", style=style, expand=False))


def _cmd_smoke(args: argparse.Namespace) -> int:
    configure_logging(json_logs=False)
    log = get_logger("smoke")
    config = _load(args.config)
    log.info("config.loaded", whitelist=config.risk.whitelist)

    gate = RiskGate(config.risk)
    NullAdvisor()  # default advisor: never gives an opinion
    state = PortfolioState()
    allowed = config.risk.whitelist[0] if config.risk.whitelist else "BTC/USDT"

    # The risk gate MUST reject invalid orders (self-check).
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
            _console.print(f"[bold red]smoke FAIL[/]: risk gate approved invalid order ({check})")
            return 1

    log.info("smoke.ok")
    _console.print("[bold green]smoke OK[/]")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    mode = _resolve_mode(args.mode)
    if mode is None:
        _console.print(
            "[bold red]error:[/] specify --mode {backtest,paper,live} (or OLIBUGUARD_MODE)"
        )
        return 2
    if mode.touches_real_money and not args.i_understand_this_is_real_money:
        _console.print(
            f"[bold red]error:[/] LIVE mode trades with REAL MONEY. "
            f"Re-run with {_LIVE_FLAG} if that is really what you want."
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
        detail="market data and order manager arrive in Phase 1-2; nothing to trade yet",
    )
    _console.print("[yellow]Phase 0: no trading loop yet. Exiting cleanly.[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # load .env if present (secrets out of the code; see .env.example)
    parser = argparse.ArgumentParser(
        prog="olibuguard",
        description="Crypto trading bot (guardrails first).",
    )
    parser.add_argument("--version", action="version", version=f"olibuguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_smoke = sub.add_parser(
        "smoke", help="Start, read config, self-check the risk gate and exit."
    )
    p_smoke.add_argument("--config", default=None, help="Path to config.yaml (optional).")

    p_run = sub.add_parser("run", help="Start the bot in a specific mode.")
    p_run.add_argument("--mode", default=None, help="backtest | paper | live")
    p_run.add_argument("--config", default=None, help="Path to config.yaml (optional).")
    p_run.add_argument(
        _LIVE_FLAG,
        dest="i_understand_this_is_real_money",
        action="store_true",
        help="Mandatory confirmation to trade LIVE with real money.",
    )

    args = parser.parse_args(argv)
    if args.command == "smoke":
        return _cmd_smoke(args)
    if args.command == "run":
        return _cmd_run(args)
    return 2
