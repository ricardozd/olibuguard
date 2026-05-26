"""Process operating modes.

Hard separation (design section 5.1): the mode is fixed at startup and never
changes at runtime. The process cannot switch from ``paper`` to ``live`` on its own.
"""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"

    @property
    def touches_real_money(self) -> bool:
        return self is Mode.LIVE
