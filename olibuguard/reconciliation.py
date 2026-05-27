"""Reconciliation helpers: restore peak equity from the audit DB and detect
unexpected equity drift between candles.

These are pure-domain functions (no Freqtrade or SQLAlchemy coupling). The
strategy adapter calls them at startup and on every bot_loop_start tick.
"""

from __future__ import annotations

from decimal import Decimal


def restore_peak_equity(current_equity: Decimal, recorded_peak: Decimal) -> Decimal:
    """Return the highest known equity across current state and recorded history.

    Called at startup so the drawdown circuit breaker is not inadvertently reset
    to zero after a bot restart (which would leave drawdown protection blind).

    Args:
        current_equity: equity read from the exchange wallet right now.
        recorded_peak:  maximum equity_quote previously stored in the audit DB.

    Returns:
        The larger of the two values.
    """
    return max(current_equity, recorded_peak)


def equity_drift_pct(last_recorded: Decimal, current: Decimal) -> Decimal:
    """Return the absolute drift as a fraction (0.05 = 5 %).

    Returns zero when *last_recorded* is not positive (avoids division by zero
    when the audit DB is empty at first run).
    """
    if last_recorded <= Decimal("0"):
        return Decimal("0")
    return abs(current - last_recorded) / last_recorded


def check_equity_drift(
    last_recorded: Decimal,
    current: Decimal,
    threshold_pct: float = 0.05,
) -> str | None:
    """Return a warning string when equity drifted beyond *threshold_pct* since
    the last recorded snapshot, otherwise return ``None``.

    A large intra-candle drift can indicate external account activity (manual
    deposits, withdrawals, trades outside the bot) that the risk gate is
    unaware of.

    Args:
        last_recorded:  equity_quote of the most recent equity-curve snapshot.
        current:        equity read from the exchange wallet right now.
        threshold_pct:  fraction threshold (default 0.05 = 5 %).

    Returns:
        A non-empty warning string, or ``None`` if drift is within tolerance.
    """
    drift = equity_drift_pct(last_recorded, current)
    if drift > Decimal(str(threshold_pct)):
        return (
            f"equity_drift_detected: last_recorded={last_recorded} "
            f"current={current} drift={float(drift):.2%}"
        )
    return None
