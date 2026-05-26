from __future__ import annotations

from olibuguard.cli import main
from olibuguard.modes import Mode


def test_mode_enum_values() -> None:
    assert {m.value for m in Mode} == {"backtest", "paper", "live"}


def test_only_live_touches_real_money() -> None:
    assert Mode.LIVE.touches_real_money
    assert not Mode.PAPER.touches_real_money
    assert not Mode.BACKTEST.touches_real_money


def test_smoke_exits_clean() -> None:
    assert main(["smoke"]) == 0


def test_run_requires_explicit_mode() -> None:
    assert main(["run"]) == 2


def test_paper_runs_clean() -> None:
    assert main(["run", "--mode", "paper"]) == 0


def test_live_requires_confirmation_flag() -> None:
    assert main(["run", "--mode", "live"]) == 2


def test_live_with_confirmation_starts() -> None:
    assert main(["run", "--mode", "live", "--i-understand-this-is-real-money"]) == 0
