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
    *max_consecutive*, the optional *kill_switch* is activated with a
    human-readable reason and the event is logged at ERROR level.

    Args:
        name:             Short identifier used in log messages and kill-switch reason.
        max_consecutive:  Number of consecutive failures before the budget is exhausted.
        kill_switch:      Optional kill switch to activate on exhaustion.
    """

    def __init__(
        self,
        name: str,
        max_consecutive: int,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self._name = name
        self._max = max_consecutive
        self._kill_switch = kill_switch
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
        """Increment counter; activate kill switch if budget is exhausted."""
        self._count += 1
        _logger.warning(
            "error_budget.increment name=%s consecutive=%d/%d exc=%s",
            self._name,
            self._count,
            self._max,
            exc,
        )
        if self.exhausted and self._kill_switch is not None:
            reason = (
                f"error_budget_exhausted: {self._name} "
                f"({self._count} consecutive errors — last: {exc})"
            )
            _logger.error(
                "error_budget.exhausted name=%s activating_kill_switch=true", self._name
            )
            self._kill_switch.activate(reason=reason)


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
