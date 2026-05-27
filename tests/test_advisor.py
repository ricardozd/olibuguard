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
            # Signal quality
            "ema_fast": 50100.0,
            "ema_slow": 49900.0,
            "rsi": 58.0,
            "volume": 1000.0,
            "volume_ratio": 1.5,
            # Last candle OHLC (close = price = 50000)
            "open": 49800.0,
            "high": 50200.0,
            "low": 49750.0,
            # Last 5 closes, newest first
            "close_0": 50000.0,
            "close_1": 49950.0,
            "close_2": 49600.0,
            "close_3": 49400.0,
            "close_4": 49800.0,
            # Portfolio state
            "equity": 990.0,
            "drawdown_pct": 0.01,
            "open_positions": 1.0,
            "open_exposure": 45.0,
            "realized_pnl_today": -9.5,
            # Risk-gate limits
            "daily_loss_limit_pct": 0.05,
            "max_drawdown_pct": 0.10,
            "max_open_positions": 3.0,
        },
    )


def _bedrock_response(veto: bool, reason: str = "") -> dict[str, Any]:
    """Standard response (no thinking block)."""
    return {"content": [{"type": "text", "text": json.dumps({"veto": veto, "reason": reason})}]}


def _bedrock_thinking_response(veto: bool, reason: str = "") -> dict[str, Any]:
    """Response with a thinking block prepended (extended thinking mode)."""
    return {
        "content": [
            {"type": "thinking", "thinking": "Let me analyse the market context…"},
            {"type": "text", "text": json.dumps({"veto": veto, "reason": reason})},
        ]
    }


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
    assert cfg.max_tokens == 8192
    assert cfg.timeout_seconds == pytest.approx(30.0)
    assert not cfg.thinking
    assert cfg.thinking_budget_tokens == 5000


def test_ai_config_thinking_budget_must_be_less_than_max_tokens() -> None:
    from olibuguard.config import AIConfig

    with pytest.raises(ValidationError):
        AIConfig(
            enabled=True,
            provider="bedrock",
            model="test",
            thinking=True,
            max_tokens=1000,
            thinking_budget_tokens=1000,  # equal → not strictly less
        )
    with pytest.raises(ValidationError):
        AIConfig(
            enabled=True,
            provider="bedrock",
            model="test",
            thinking=True,
            max_tokens=1000,
            thinking_budget_tokens=2000,  # greater → invalid
        )


def test_ai_config_thinking_budget_valid_when_less() -> None:
    from olibuguard.config import AIConfig

    cfg = AIConfig(
        enabled=True,
        provider="bedrock",
        model="test",
        thinking=True,
        max_tokens=8192,
        thinking_budget_tokens=5000,
    )
    assert cfg.thinking
    assert cfg.thinking_budget_tokens == 5000


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
    # type=text so we reach the JSON-parse branch, not the no-text-block branch.
    body.read.return_value = json.dumps(
        {"content": [{"type": "text", "text": "not json"}]}
    ).encode()
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
    # Setting a module to None in sys.modules makes Python treat it as forbidden:
    # any "import boto3" will raise ImportError even when the package is installed.
    sys.modules.pop("olibuguard.advisor.bedrock", None)
    saved = sys.modules.get("boto3")
    sys.modules["boto3"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(ImportError, match="boto3"):
            from olibuguard.advisor.bedrock import BedrockAdvisor

            BedrockAdvisor(model_id="m")
    finally:
        sys.modules.pop("olibuguard.advisor.bedrock", None)
        if saved is not None:
            sys.modules["boto3"] = saved
        else:
            sys.modules.pop("boto3", None)


# ── Prompt content ───────────────────────────────────────────────────────────


def test_bedrock_prompt_contains_all_sections(fake_boto3: Any) -> None:
    """The rendered prompt sent to Bedrock includes every context section."""
    fake_boto3.client.return_value = _mock_client(_bedrock_response(veto=False))
    from olibuguard.advisor.bedrock import BedrockAdvisor

    BedrockAdvisor(model_id="m").opinion(_ctx())

    body: dict[str, Any] = json.loads(
        fake_boto3.client.return_value.invoke_model.call_args.kwargs["body"]
    )
    prompt: str = body["messages"][0]["content"]
    for section in ("Signal quality", "Recent closes", "Portfolio state",
                    "EMA separation", "RSI", "Volume ratio",
                    "Drawdown", "Daily P&L"):
        assert section in prompt, f"missing section: {section!r}"


def test_bedrock_prompt_ema_separation_computed(fake_boto3: Any) -> None:
    """EMA separation % is computed and injected into the prompt."""
    fake_boto3.client.return_value = _mock_client(_bedrock_response(veto=False))
    from olibuguard.advisor.bedrock import BedrockAdvisor

    BedrockAdvisor(model_id="m").opinion(_ctx())
    body: dict[str, Any] = json.loads(
        fake_boto3.client.return_value.invoke_model.call_args.kwargs["body"]
    )
    prompt: str = body["messages"][0]["content"]
    # ema_fast=50100, ema_slow=49900, price=50000 → sep = 0.40% (moderate)
    assert "moderate" in prompt
    assert "+0.40%" in prompt


def test_bedrock_prompt_rsi_descriptor(fake_boto3: Any) -> None:
    """RSI descriptor maps: >75 overbought, <25 oversold, else neutral."""
    from olibuguard.advisor.bedrock import BedrockAdvisor

    for rsi_val, expected in [(80.0, "overbought"), (20.0, "oversold"), (50.0, "neutral")]:
        ctx = MarketContext(
            symbol="BTC/USDT",
            timestamp=datetime.now(UTC),
            price=Decimal("50000"),
            indicators={**_ctx().indicators, "rsi": rsi_val},
        )
        fake_boto3.client.return_value = _mock_client(_bedrock_response(veto=False))
        BedrockAdvisor(model_id="m").opinion(ctx)
        body: dict[str, Any] = json.loads(
            fake_boto3.client.return_value.invoke_model.call_args.kwargs["body"]
        )
        assert expected in body["messages"][0]["content"]


# ── Extended thinking ─────────────────────────────────────────────────────────


def test_bedrock_advisor_thinking_veto(fake_boto3: Any) -> None:
    """Thinking response: veto=true inside the text block after the thinking block."""
    fake_boto3.client.return_value = _mock_client(
        _bedrock_thinking_response(veto=True, reason="sharp drop detected")
    )
    from olibuguard.advisor.bedrock import BedrockAdvisor

    opinion = BedrockAdvisor(model_id="m", thinking=True).opinion(_ctx())
    assert opinion is not None
    assert opinion.bias == -1.0
    assert "sharp drop" in opinion.rationale


def test_bedrock_advisor_thinking_no_veto(fake_boto3: Any) -> None:
    """Thinking response: veto=false → advisor abstains (returns None)."""
    fake_boto3.client.return_value = _mock_client(
        _bedrock_thinking_response(veto=False, reason="conditions normal")
    )
    from olibuguard.advisor.bedrock import BedrockAdvisor

    assert BedrockAdvisor(model_id="m", thinking=True).opinion(_ctx()) is None


def test_bedrock_advisor_thinking_block_ignored(fake_boto3: Any) -> None:
    """Content that has only a thinking block (no text block) → abstain, not crash."""
    body = MagicMock()
    body.read.return_value = json.dumps(
        {"content": [{"type": "thinking", "thinking": "some reasoning"}]}
    ).encode()
    client = MagicMock()
    client.invoke_model.return_value = {"body": body}
    fake_boto3.client.return_value = client
    from olibuguard.advisor.bedrock import BedrockAdvisor

    assert BedrockAdvisor(model_id="m", thinking=True).opinion(_ctx()) is None


def test_bedrock_advisor_thinking_request_includes_budget(fake_boto3: Any) -> None:
    """When thinking=True, invoke_model is called with the thinking block in the body."""
    fake_boto3.client.return_value = _mock_client(
        _bedrock_thinking_response(veto=False)
    )
    from olibuguard.advisor.bedrock import BedrockAdvisor

    BedrockAdvisor(model_id="m", thinking=True, thinking_budget_tokens=2000).opinion(_ctx())

    call_kwargs = fake_boto3.client.return_value.invoke_model.call_args
    sent_body: dict[str, Any] = json.loads(call_kwargs.kwargs["body"])
    assert sent_body.get("thinking") == {"type": "enabled", "budget_tokens": 2000}


def test_bedrock_advisor_no_thinking_request_omits_budget(fake_boto3: Any) -> None:
    """When thinking=False, the thinking key must not appear in the request body."""
    fake_boto3.client.return_value = _mock_client(_bedrock_response(veto=False))
    from olibuguard.advisor.bedrock import BedrockAdvisor

    BedrockAdvisor(model_id="m", thinking=False).opinion(_ctx())

    call_kwargs = fake_boto3.client.return_value.invoke_model.call_args
    sent_body: dict[str, Any] = json.loads(call_kwargs.kwargs["body"])
    assert "thinking" not in sent_body
