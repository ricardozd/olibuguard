"""Modos de operación del proceso.

Separación dura (sección 5.1 del diseño): el modo se fija al arrancar y no
cambia en runtime. El binario no puede pasar de ``paper`` a ``live`` solo.
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
