"""Fail-safe utilities for wrapping external calls that must not block trading.

``run_safe`` is a convenience wrapper that calls a zero-argument callable,
catches any exception, logs it with the operation name, and returns a
caller-supplied default value. It never raises.

``ErrorBudget`` counts consecutive errors for a named operation; when the
budget is exhausted it activates the kill switch so the bot stops opening
new positions until an operator intervenes. Successful calls reset the counter.

Typical usage in the strategy adapter::

    equity = run_safe(
        "equity_read",
        lambda: Decimal(str(wallets.get_total_stake_amount())),
        default=Decimal("0"),
        budget=self._equity_budget,
    )
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from olibuguard.kill_switch import KillSwitch

_logger = logging.getLogger(__name__)


class ErrorBudget:
    """Track consecutive errors for a named operation.

    On success, the counter resets to zero. When consecutive errors reach
    *max_consecutive* (exactly), the optional *kill_switch* is activated and
    *on_exhausted* is called — both fire exactly once at the threshold.

    Args:
        name:             Short identifier for log messages and the kill-switch reason.
        max_consecutive:  Number of consecutive failures before exhaustion.
        kill_switch:      Optional kill switch to activate on exhaustion.
        on_exhausted:     Optional zero-argument callback invoked exactly once at
                          exhaustion (e.g. to send an alert).
    """

    def __init__(
        self,
        name: str,
        max_consecutive: int,
        kill_switch: KillSwitch | None = None,
        on_exhausted: Callable[[], None] | None = None,
    ) -> None:
        self._name = name
        self._max = max_consecutive
        self._kill_switch = kill_switch
        self._on_exhausted = on_exhausted
        self._count = 0

    @property
    def consecutive_errors(self) -> int:
        """Current consecutive-error count."""
        return self._count

    @property
    def exhausted(self) -> bool:
        """True when consecutive errors have reached *max_consecutive*."""
        return self._count >= self._max

    def record_success(self) -> None:
        """Reset the consecutive-error counter."""
        self._count = 0

    def record_error(self, exc: BaseException) -> None:
        """Increment counter; activate kill switch and fire on_exhausted at threshold."""
        self._count += 1
        _logger.warning(
            "error_budget.increment name=%s consecutive=%d/%d exc=%s",
            self._name,
            self._count,
            self._max,
            exc,
        )
        # Fire exactly once when we reach the threshold.
        if self._count == self._max:
            reason = (
                f"error_budget_exhausted: {self._name} "
                f"({self._count} consecutive errors — last: {exc})"
            )
            _logger.error(
                "error_budget.exhausted name=%s activating_kill_switch=true", self._name
            )
            if self._kill_switch is not None:
                self._kill_switch.activate(reason=reason)
            if self._on_exhausted is not None:
                self._on_exhausted()


def run_safe[T](
    op: str,
    fn: Callable[[], T],
    default: T,
    *,
    budget: ErrorBudget | None = None,
) -> T:
    """Call *fn()*; on any exception return *default* (never raises).

    Args:
        op:      Short operation name for log messages.
        fn:      Zero-argument callable to execute.
        default: Value returned when *fn* raises.
        budget:  Optional :class:`ErrorBudget` to track consecutive failures.

    Returns:
        The result of *fn()*, or *default* if it raised.
    """
    try:
        result = fn()
        if budget is not None:
            budget.record_success()
        return result
    except Exception as exc:
        _logger.warning(
            "failsafe.caught op=%s exc_type=%s detail=%s",
            op,
            type(exc).__name__,
            exc,
        )
        if budget is not None:
            budget.record_error(exc)
        return default
