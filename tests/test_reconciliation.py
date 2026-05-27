from __future__ import annotations

from decimal import Decimal

import pytest

from olibuguard.reconciliation import (
    check_equity_drift,
    equity_drift_pct,
    restore_peak_equity,
)

# ── restore_peak_equity ─────────────────────────────────────────────────────


def test_restore_uses_recorded_peak_when_higher() -> None:
    assert restore_peak_equity(Decimal("900"), Decimal("1000")) == Decimal("1000")


def test_restore_uses_current_when_higher() -> None:
    assert restore_peak_equity(Decimal("1100"), Decimal("1000")) == Decimal("1100")


def test_restore_equal_values() -> None:
    assert restore_peak_equity(Decimal("1000"), Decimal("1000")) == Decimal("1000")


def test_restore_with_zero_recorded() -> None:
    """First-run: no history in DB → peak equals current equity."""
    assert restore_peak_equity(Decimal("800"), Decimal("0")) == Decimal("800")


# ── equity_drift_pct ────────────────────────────────────────────────────────


def test_drift_pct_zero_when_last_recorded_is_zero() -> None:
    assert equity_drift_pct(Decimal("0"), Decimal("1000")) == Decimal("0")


def test_drift_pct_computes_correctly() -> None:
    pct = equity_drift_pct(Decimal("1000"), Decimal("950"))
    assert pct == pytest.approx(Decimal("0.05"), rel=Decimal("1e-9"))


def test_drift_pct_symmetric() -> None:
    """Drift is absolute, so gains and losses have the same magnitude."""
    down = equity_drift_pct(Decimal("1000"), Decimal("900"))
    up = equity_drift_pct(Decimal("1000"), Decimal("1100"))
    assert down == up


# ── check_equity_drift ──────────────────────────────────────────────────────


def test_no_warning_within_threshold() -> None:
    # 3% drop, threshold 5% → no warning
    assert check_equity_drift(Decimal("1000"), Decimal("970"), threshold_pct=0.05) is None


def test_warning_above_threshold() -> None:
    # 10% drop, threshold 5% → warning
    result = check_equity_drift(Decimal("1000"), Decimal("900"), threshold_pct=0.05)
    assert result is not None
    assert "equity_drift_detected" in result
    assert "10.00%" in result


def test_no_warning_when_last_recorded_zero() -> None:
    """Empty DB at first run must not produce a spurious drift warning."""
    assert check_equity_drift(Decimal("0"), Decimal("1000")) is None


def test_warning_on_unexpected_gain() -> None:
    # 20% gain (e.g. manual deposit)
    result = check_equity_drift(Decimal("1000"), Decimal("1200"), threshold_pct=0.05)
    assert result is not None
    assert "equity_drift_detected" in result


def test_custom_threshold() -> None:
    # 3% drop, threshold 2% → warning
    result = check_equity_drift(Decimal("1000"), Decimal("970"), threshold_pct=0.02)
    assert result is not None
