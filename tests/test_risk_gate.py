from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from olibuguard.config import RiskLimits
from olibuguard.domain.models import OrderIntent, PortfolioState, Side
from olibuguard.risk.gate import RiskGate


def _limits(
    *,
    max_risk_per_trade_pct: float = 1.0,
    max_position_quote: Decimal = Decimal("100"),
    max_total_exposure_quote: Decimal = Decimal("1000"),
    max_open_positions: int = 5,
    min_order_quote: Decimal = Decimal("10"),
    max_orders_per_minute: int = 10,
    max_slippage_pct: float = 0.005,
    daily_loss_limit_pct: float = 0.05,
    max_drawdown_pct: float = 0.10,
    whitelist: list[str] | None = None,
    blacklist: list[str] | None = None,
) -> RiskLimits:
    return RiskLimits(
        max_risk_per_trade_pct=max_risk_per_trade_pct,
        max_position_quote=max_position_quote,
        max_total_exposure_quote=max_total_exposure_quote,
        max_open_positions=max_open_positions,
        min_order_quote=min_order_quote,
        max_orders_per_minute=max_orders_per_minute,
        max_slippage_pct=max_slippage_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
        max_drawdown_pct=max_drawdown_pct,
        whitelist=whitelist if whitelist is not None else ["BTC/USDT"],
        blacklist=blacklist if blacklist is not None else ["SCAM/USDT"],
    )


def _buy(symbol: str, amount: Decimal) -> OrderIntent:
    return OrderIntent(
        symbol=symbol, side=Side.BUY, quote_amount=amount, reference_price=Decimal("1")
    )


# --- Hard limits (absolute caps) ---


def test_rejects_blacklisted_pair() -> None:
    gate = RiskGate(_limits())
    verdict = gate.evaluate(_buy("SCAM/USDT", Decimal("50")), PortfolioState())
    assert not verdict.approved
    assert "blacklist" in verdict.reason


def test_rejects_out_of_whitelist() -> None:
    gate = RiskGate(_limits())
    verdict = gate.evaluate(_buy("DOGE/USDT", Decimal("50")), PortfolioState())
    assert not verdict.approved
    assert "whitelist" in verdict.reason


def test_caps_oversize_to_max_position() -> None:
    gate = RiskGate(_limits(max_position_quote=Decimal("100")))
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("500")), PortfolioState())
    assert verdict.approved
    assert verdict.intent is not None
    assert verdict.intent.quote_amount == Decimal("100")


def test_caps_to_available_exposure() -> None:
    gate = RiskGate(_limits(max_total_exposure_quote=Decimal("1000")))
    state = PortfolioState(open_exposure_quote=Decimal("960"))
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("100")), state)
    assert verdict.approved
    assert verdict.intent is not None
    assert verdict.intent.quote_amount == Decimal("40")


def test_rejects_when_too_small() -> None:
    gate = RiskGate(_limits(min_order_quote=Decimal("10")))
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("5")), PortfolioState())
    assert not verdict.approved


def test_rejects_when_max_positions_reached() -> None:
    gate = RiskGate(_limits(max_open_positions=2))
    state = PortfolioState(open_positions=2)
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("50")), state)
    assert not verdict.approved


def test_rejects_when_order_rate_exceeded() -> None:
    gate = RiskGate(_limits(max_orders_per_minute=3))
    state = PortfolioState(orders_last_minute=3)
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("50")), state)
    assert not verdict.approved


def test_approves_valid_order_unchanged() -> None:
    gate = RiskGate(_limits())
    intent = _buy("BTC/USDT", Decimal("50"))
    verdict = gate.evaluate(intent, PortfolioState())
    assert verdict.approved
    assert verdict.intent == intent


# --- Dynamic sizing (% of capital) ---


def test_position_sizing_caps_to_pct_of_equity() -> None:
    gate = RiskGate(_limits(max_risk_per_trade_pct=0.02, max_position_quote=Decimal("100000")))
    state = PortfolioState(equity_quote=Decimal("1000"), peak_equity_quote=Decimal("1000"))
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("500")), state)
    assert verdict.approved
    assert verdict.intent is not None
    assert verdict.intent.quote_amount == Decimal("20")  # 2% of 1000


# --- Circuit breakers (automatic kill-switch) ---


def test_circuit_breaker_drawdown_halts() -> None:
    gate = RiskGate(_limits(max_drawdown_pct=0.10))
    state = PortfolioState(equity_quote=Decimal("900"), peak_equity_quote=Decimal("1000"))
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("50")), state)
    assert not verdict.approved
    assert "drawdown" in verdict.reason


def test_circuit_breaker_daily_loss_halts() -> None:
    gate = RiskGate(_limits(daily_loss_limit_pct=0.05))
    state = PortfolioState(
        equity_quote=Decimal("1000"),
        peak_equity_quote=Decimal("1000"),
        realized_pnl_today_quote=Decimal("-60"),
    )
    verdict = gate.evaluate(_buy("BTC/USDT", Decimal("50")), state)
    assert not verdict.approved
    assert "daily loss" in verdict.reason


# --- Slippage ---


def test_slippage_veto() -> None:
    gate = RiskGate(_limits(max_slippage_pct=0.005))
    intent = OrderIntent(
        symbol="BTC/USDT",
        side=Side.BUY,
        quote_amount=Decimal("50"),
        reference_price=Decimal("100"),
        execution_price=Decimal("100.6"),
    )
    verdict = gate.evaluate(intent, PortfolioState())
    assert not verdict.approved
    assert "slippage" in verdict.reason


def test_slippage_within_tolerance_ok() -> None:
    gate = RiskGate(_limits(max_slippage_pct=0.01))
    intent = OrderIntent(
        symbol="BTC/USDT",
        side=Side.BUY,
        quote_amount=Decimal("50"),
        reference_price=Decimal("100"),
        execution_price=Decimal("100.5"),
    )
    verdict = gate.evaluate(intent, PortfolioState())
    assert verdict.approved


# --- Properties ---


@given(
    amount=st.decimals(
        min_value=Decimal("1"),
        max_value=Decimal("10000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    exposure=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("1000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_property_never_increases_and_respects_caps(amount: Decimal, exposure: Decimal) -> None:
    limits = _limits()
    gate = RiskGate(limits)
    state = PortfolioState(open_exposure_quote=exposure)
    verdict = gate.evaluate(_buy("BTC/USDT", amount), state)
    if verdict.approved:
        assert verdict.intent is not None
        approved = verdict.intent.quote_amount
        assert approved <= amount  # never increases
        assert approved <= limits.max_position_quote  # per-trade cap
        assert exposure + approved <= limits.max_total_exposure_quote  # total exposure


@given(
    amount=st.decimals(
        min_value=Decimal("1"),
        max_value=Decimal("100000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    equity=st.decimals(
        min_value=Decimal("100"),
        max_value=Decimal("100000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_property_sizing_never_exceeds_risk_cap(amount: Decimal, equity: Decimal) -> None:
    limits = _limits(
        max_risk_per_trade_pct=0.02,
        max_position_quote=Decimal("100000"),
        max_total_exposure_quote=Decimal("100000000"),
    )
    gate = RiskGate(limits)
    state = PortfolioState(equity_quote=equity, peak_equity_quote=equity)
    verdict = gate.evaluate(_buy("BTC/USDT", amount), state)
    if verdict.approved:
        assert verdict.intent is not None
        risk_cap = equity * Decimal("0.02")
        assert verdict.intent.quote_amount <= risk_cap
        assert verdict.intent.quote_amount <= amount
