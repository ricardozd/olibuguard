from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from olibuguard.advisor.base import AIAdvisor, NullAdvisor, clamp_advisor_factor
from olibuguard.domain.models import MarketContext


def _ctx() -> MarketContext:
    return MarketContext(symbol="BTC/USDT", timestamp=datetime.now(UTC), price=Decimal("100"))


def test_null_advisor_returns_none() -> None:
    assert NullAdvisor().opinion(_ctx()) is None


def test_null_advisor_satisfies_protocol() -> None:
    assert isinstance(NullAdvisor(), AIAdvisor)


def test_advisor_can_only_reduce_never_amplify() -> None:
    assert clamp_advisor_factor(1.0) == 1.0
    assert clamp_advisor_factor(0.5) == 1.0  # positive bias does NOT increase
    assert clamp_advisor_factor(0.0) == 1.0
    assert clamp_advisor_factor(-0.5) == 0.5
    assert clamp_advisor_factor(-1.0) == 0.0  # full veto
