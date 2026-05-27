"""AWS Bedrock advisor — calls Claude to veto trades with obvious red flags.

Only loaded when ai.enabled = true and ai.provider = "bedrock" in config.yaml.
If boto3 is not installed or Bedrock is unreachable the advisor returns None
(fail-safe: the trade proceeds without AI interference).

AWS credentials — choose one:
  • Named profile (role-based, recommended for local dev):
      ai.profile: a9e  in config.yaml  → assumes the role via ~/.aws/config
  • Default credential chain (EC2 instance role, ECS task role, env vars):
      leave ai.profile unset; boto3 picks up credentials automatically.
  • Static keys (not recommended, last resort):
      AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env
"""

from __future__ import annotations

import json
import logging
from typing import Any

from olibuguard.advisor.base import AdvisorOpinion
from olibuguard.domain.models import MarketContext

_logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a conservative risk advisor for a crypto trading bot. "
    "Your only role is to veto trades with extreme, objective red flags. "
    "Think step by step: check for price anomalies, abnormal volume, equity stress, "
    "and whether the market context is clearly dangerous or anomalous. "
    "When in doubt do NOT veto — a missed bad trade is better than a false veto "
    "that blocks a good one. "
    "After your analysis output a single JSON object and nothing else:\n"
    '{"veto": false, "reason": "brief explanation"}\n'
    "or\n"
    '{"veto": true, "reason": "specific red flag observed"}'
)

_USER_TEMPLATE = """\
The strategy signals a BUY (EMA 20 crossed above EMA 50, 1-hour timeframe).

Market context:
- Symbol: {symbol}
- Current price: {price} USDT
- EMA 20 (fast): {ema_fast:.4f}
- EMA 50 (slow): {ema_slow:.4f}
- Volume (last candle): {volume:.2f}
- Portfolio equity: {equity:.2f} USDT
- Drawdown from peak: {drawdown_pct:.1%}

Veto only for extreme risk: price collapsed > 5% in the last candle, equity is at
or very near a circuit-breaker limit, or the data looks clearly anomalous.

Respond with JSON only:
{{"veto": false, "reason": "..."}}
or
{{"veto": true, "reason": "..."}}"""


class BedrockAdvisor:
    """Calls AWS Bedrock (Claude) to optionally veto a proposed BUY.

    Satisfies the AIAdvisor Protocol. Any Bedrock or network error returns None
    (fail-safe — the trade proceeds as if no advisor was present).
    """

    def __init__(
        self,
        model_id: str,
        region: str = "eu-west-1",
        profile: str | None = None,
        max_tokens: int = 8192,
        thinking: bool = False,
        thinking_budget_tokens: int = 5000,
        timeout_seconds: float = 30.0,
    ) -> None:
        try:
            import boto3

            if profile:
                # Role-based auth: assume the role declared in ~/.aws/config.
                session = boto3.Session(profile_name=profile)
                self._client: Any = session.client("bedrock-runtime", region_name=region)
            else:
                # Default credential chain (instance role, env vars, …).
                self._client = boto3.client("bedrock-runtime", region_name=region)
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for BedrockAdvisor. "
                "Install it with: uv sync --extra ai"
            ) from exc
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._thinking_budget = thinking_budget_tokens
        self._timeout = timeout_seconds

    def opinion(self, context: MarketContext) -> AdvisorOpinion | None:
        """Ask Claude whether to veto this trade.

        Returns ``AdvisorOpinion(bias=-1.0)`` to veto or ``None`` to abstain.
        Never raises — any error is logged and treated as abstention.
        """
        try:
            return self._call(context)
        except Exception as exc:
            _logger.warning("bedrock_advisor.error: %s — abstaining (fail-safe)", exc)
            return None

    # ── internals ────────────────────────────────────────────────────────────

    def _call(self, context: MarketContext) -> AdvisorOpinion | None:
        prompt = _USER_TEMPLATE.format(
            symbol=context.symbol,
            price=float(context.price),
            ema_fast=context.indicators.get("ema_fast", 0.0),
            ema_slow=context.indicators.get("ema_slow", 0.0),
            volume=context.indicators.get("volume", 0.0),
            equity=context.indicators.get("equity", 0.0),
            drawdown_pct=context.indicators.get("drawdown_pct", 0.0),
        )
        body_dict: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._max_tokens,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._thinking:
            # Extended thinking: Claude reasons step-by-step before the verdict.
            # budget_tokens < max_tokens is enforced by AIConfig validator.
            body_dict["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }
        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body_dict),
        )
        raw: dict[str, Any] = json.loads(response["body"].read())
        # Thinking responses include a {"type": "thinking", ...} block before the
        # text block.  Find the text block by type rather than by index so the
        # same code path works with and without thinking enabled.
        text_block = next(
            (b for b in raw.get("content", []) if b.get("type") == "text"),
            None,
        )
        if text_block is None:
            _logger.warning("bedrock_advisor.no_text_block — abstaining")
            return None
        text: str = text_block["text"].strip()
        try:
            parsed: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            _logger.warning(
                "bedrock_advisor.invalid_json: %r — abstaining", text[:200]
            )
            return None
        veto = bool(parsed.get("veto", False))
        reason = str(parsed.get("reason", ""))
        if veto:
            _logger.info("bedrock_advisor.veto: %s — %s", context.symbol, reason)
            return AdvisorOpinion(bias=-1.0, rationale=reason)
        _logger.debug("bedrock_advisor.pass: %s — %s", context.symbol, reason)
        return None  # abstain — don't interfere with the trade
