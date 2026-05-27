"""Tests for the AI advisor layer (Phase 3).

BedrockAdvisor tests inject a fake boto3 via sys.modules so the tests run
without an actual AWS account or boto3 installed.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from olibuguard.advisor.base import AdvisorOpinion, AIAdvisor, NullAdvisor, clamp_advisor_factor
from olibuguard.domain.models import MarketContext

# ── helpers ───────────────────────────────────────────────────────────────────


def _ctx() -> MarketContext:
    return MarketContext(
        symbol="BTC/USDT",
        timestamp=datetime.now(UTC),
        price=Decimal("50000"),
        indicators={
            "ema_fast": 50100.0,
            "ema_slow": 49900.0,
            "volume": 1000.0,
            "equity": 990.0,
            "drawdown_pct": 0.01,
        },
    )


def _bedrock_response(veto: bool, reason: str = "") -> dict[str, Any]:
    return {"content": [{"text": json.dumps({"veto": veto, "reason": reason})}]}


def _mock_client(response: dict[str, Any]) -> MagicMock:
    body = MagicMock()
    body.read.return_value = json.dumps(response).encode()
    client = MagicMock()
    client.invoke_model.return_value = {"body": body}
    return client


@pytest.fixture()
def fake_boto3() -> Any:
    """Inject a fake boto3 into sys.modules and clean up the bedrock module cache."""
    mock: MagicMock = MagicMock()
    sys.modules.pop("olibuguard.advisor.bedrock", None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "boto3", mock)
        yield mock
    sys.modules.pop("olibuguard.advisor.bedrock", None)


# ── NullAdvisor ───────────────────────────────────────────────────────────────


def test_null_advisor_returns_none() -> None:
    assert NullAdvisor().opinion(_ctx()) is None


def test_null_advisor_satisfies_protocol() -> None:
    assert isinstance(NullAdvisor(), AIAdvisor)


# ── clamp_advisor_factor ──────────────────────────────────────────────────────


def test_advisor_can_only_reduce_never_amplify() -> None:
    assert clamp_advisor_factor(1.0) == 1.0
    assert clamp_advisor_factor(0.5) == 1.0  # positive bias does NOT increase
    assert clamp_advisor_factor(0.0) == 1.0
    assert clamp_advisor_factor(-0.5) == pytest.approx(0.5)
    assert clamp_advisor_factor(-1.0) == 0.0  # full veto


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_clamp_positive_never_enlarges(bias: float) -> None:
    assert clamp_advisor_factor(bias) == 1.0


@given(st.floats(min_value=-1.0, max_value=0.0, allow_nan=False))
def test_clamp_negative_stays_in_range(bias: float) -> None:
    assert 0.0 <= clamp_advisor_factor(bias) <= 1.0


# ── AdvisorOpinion ────────────────────────────────────────────────────────────


def test_advisor_opinion_is_immutable() -> None:
    op = AdvisorOpinion(bias=-1.0, rationale="test")
    with pytest.raises(ValidationError):
        op.bias = 0.0  # type: ignore[misc]


def test_advisor_opinion_rejects_invalid_bias() -> None:
    with pytest.raises(ValidationError):
        AdvisorOpinion(bias=1.5)
    with pytest.raises(ValidationError):
        AdvisorOpinion(bias=-2.0)


# ── AIConfig feature flag ─────────────────────────────────────────────────────


def test_ai_config_disabled_by_default() -> None:
    from olibuguard.config import AIConfig

    cfg = AIConfig()
    assert not cfg.enabled
    assert cfg.provider == "null"


def test_ai_config_bedrock_fields() -> None:
    from olibuguard.config import AIConfig

    cfg = AIConfig(enabled=True, provider="bedrock", model="test", region="eu-west-1")
    assert cfg.enabled
    assert cfg.max_tokens == 256
    assert cfg.timeout_seconds == pytest.approx(10.0)


# ── BedrockAdvisor ────────────────────────────────────────────────────────────


def test_bedrock_advisor_satisfies_protocol(fake_boto3: Any) -> None:
    fake_boto3.client.return_value = MagicMock()
    from olibuguard.advisor.bedrock import BedrockAdvisor

    assert isinstance(BedrockAdvisor(model_id="m"), AIAdvisor)


def test_bedrock_advisor_veto(fake_boto3: Any) -> None:
    fake_boto3.client.return_value = _mock_client(
        _bedrock_response(veto=True, reason="extreme volatility")
    )
    from olibuguard.advisor.bedrock import BedrockAdvisor

    opinion = BedrockAdvisor(model_id="m").opinion(_ctx())
    assert opinion is not None
    assert opinion.bias == -1.0
    assert "extreme volatility" in opinion.rationale


def test_bedrock_advisor_no_veto_returns_none(fake_boto3: Any) -> None:
    fake_boto3.client.return_value = _mock_client(
        _bedrock_response(veto=False, reason="conditions normal")
    )
    from olibuguard.advisor.bedrock import BedrockAdvisor

    assert BedrockAdvisor(model_id="m").opinion(_ctx()) is None


def test_bedrock_advisor_invalid_json_returns_none(fake_boto3: Any) -> None:
    body = MagicMock()
    body.read.return_value = json.dumps({"content": [{"text": "not json"}]}).encode()
    client = MagicMock()
    client.invoke_model.return_value = {"body": body}
    fake_boto3.client.return_value = client
    from olibuguard.advisor.bedrock import BedrockAdvisor

    assert BedrockAdvisor(model_id="m").opinion(_ctx()) is None


def test_bedrock_advisor_network_error_returns_none(fake_boto3: Any) -> None:
    client = MagicMock()
    client.invoke_model.side_effect = RuntimeError("connection refused")
    fake_boto3.client.return_value = client
    from olibuguard.advisor.bedrock import BedrockAdvisor

    assert BedrockAdvisor(model_id="m").opinion(_ctx()) is None


def test_bedrock_advisor_missing_boto3_raises() -> None:
    sys.modules.pop("olibuguard.advisor.bedrock", None)
    saved = sys.modules.pop("boto3", None)
    try:
        with pytest.raises(ImportError, match="boto3"):
            from olibuguard.advisor.bedrock import BedrockAdvisor

            BedrockAdvisor(model_id="m")
    finally:
        sys.modules.pop("olibuguard.advisor.bedrock", None)
        if saved is not None:
            sys.modules["boto3"] = saved
