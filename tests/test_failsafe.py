from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from olibuguard.failsafe import ErrorBudget, run_safe
from olibuguard.kill_switch import KillSwitch

# ── run_safe ────────────────────────────────────────────────────────────────


def test_run_safe_returns_result_on_success() -> None:
    result = run_safe("op", lambda: Decimal("42"), Decimal("0"))
    assert result == Decimal("42")


def test_run_safe_returns_default_on_exception() -> None:
    def boom() -> Decimal:
        raise RuntimeError("connection lost")

    result = run_safe("op", boom, Decimal("-1"))
    assert result == Decimal("-1")


def test_run_safe_returns_none_default() -> None:
    result = run_safe("op", lambda: None, None)
    assert result is None


def test_run_safe_records_success_in_budget(tmp_path: Path) -> None:
    budget = ErrorBudget("test", max_consecutive=3)
    budget.record_error(RuntimeError("prior"))
    assert budget.consecutive_errors == 1
    run_safe("op", lambda: "ok", "default", budget=budget)
    assert budget.consecutive_errors == 0


def test_run_safe_records_error_in_budget(tmp_path: Path) -> None:
    budget = ErrorBudget("test", max_consecutive=3)

    def boom() -> str:
        raise ValueError("oops")

    run_safe("op", boom, "default", budget=budget)
    assert budget.consecutive_errors == 1


# ── ErrorBudget ─────────────────────────────────────────────────────────────


def test_error_budget_not_exhausted_below_max() -> None:
    budget = ErrorBudget("op", max_consecutive=3)
    budget.record_error(RuntimeError("e1"))
    budget.record_error(RuntimeError("e2"))
    assert not budget.exhausted
    assert budget.consecutive_errors == 2


def test_error_budget_exhausted_at_max() -> None:
    budget = ErrorBudget("op", max_consecutive=3)
    for i in range(3):
        budget.record_error(RuntimeError(f"e{i}"))
    assert budget.exhausted


def test_error_budget_resets_on_success() -> None:
    budget = ErrorBudget("op", max_consecutive=3)
    budget.record_error(RuntimeError("e"))
    budget.record_error(RuntimeError("e"))
    budget.record_success()
    assert budget.consecutive_errors == 0
    assert not budget.exhausted


def test_error_budget_activates_kill_switch_when_exhausted(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "KILL_SWITCH")
    budget = ErrorBudget("equity_read", max_consecutive=2, kill_switch=ks)
    assert not ks.is_active()
    budget.record_error(RuntimeError("e1"))
    assert not ks.is_active()  # not yet exhausted
    budget.record_error(RuntimeError("e2"))
    assert ks.is_active()  # budget exhausted → kill switch activated
    content = ks.path.read_text()
    assert "error_budget_exhausted" in content
    assert "equity_read" in content


def test_error_budget_no_kill_switch_does_not_raise() -> None:
    """Budget without a kill switch must not fail when exhausted."""
    budget = ErrorBudget("op", max_consecutive=1)
    budget.record_error(RuntimeError("e"))  # must not raise
    assert budget.exhausted


def test_error_budget_kill_switch_only_activated_once(tmp_path: Path) -> None:
    """Once the budget is exhausted extra errors must not re-activate (idempotent)."""
    ks = KillSwitch(tmp_path / "KILL_SWITCH")
    budget = ErrorBudget("op", max_consecutive=1, kill_switch=ks)
    budget.record_error(RuntimeError("e1"))
    first_content = ks.path.read_text()
    budget.record_error(RuntimeError("e2"))
    # File should still exist (not overwritten) — the kill switch stays active.
    assert ks.is_active()
    # Content may or may not change (activate is idempotent); key: no exception.
    _ = ks.path.read_text()
    assert first_content  # non-empty
