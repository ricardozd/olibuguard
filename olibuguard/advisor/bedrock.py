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

=== Signal quality ===
- Symbol:           {symbol}
- Entry price:      {price:.2f} USDT
- EMA 20 (fast):    {ema_fast:.2f}   EMA 50 (slow): {ema_slow:.2f}
- EMA separation:   {ema_sep_pct:+.2f}%  ({ema_sep_desc})
- Entry candle:     {candle_dir}  (open {open_p:.2f} → close {price:.2f}, {chg:+.2f}%)
- Volume ratio:     {vol_ratio:.1f}x 20-candle average
- RSI 14:           {rsi:.1f}  ({rsi_desc})

=== Recent closes (newest first, 1h candles) ===
  {recent_closes}

=== Portfolio state ===
- Equity:           {equity:.2f} USDT
- Open positions:   {open_positions} of {max_positions} max
- Open exposure:    {open_exposure:.2f} USDT
- Drawdown:         {drawdown_pct:.1%}  (circuit breaker: {max_drawdown_pct:.0%})
- Daily P&L:        {pnl:+.2f} USDT  ({dl_frac:.1%} of {dl_lim:.0%} daily limit)

Veto only for extreme, objective red flags such as:
- RSI > 80 with weak volume (ratio < 0.5)
- Entry candle strongly bearish (close well below open, > 3% drop)
- Drawdown or daily loss already consuming > 70% of their circuit-breaker limit
- Price data clearly anomalous
When in doubt do NOT veto — false vetoes are more costly than missed bad trades.\
"""


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

    def _format_prompt(self, context: MarketContext) -> str:
        """Render the user prompt with all available market and portfolio context."""
        g = context.indicators.get
        price = float(context.price)

        ema_fast = g("ema_fast", price)
        ema_slow = g("ema_slow", price)
        open_price = g("open", price)
        equity = g("equity", 0.0)

        # EMA separation as % of price — indicates crossover strength.
        ema_sep_pct = (ema_fast - ema_slow) / price * 100.0 if price > 0 else 0.0
        if abs(ema_sep_pct) < 0.1:
            ema_sep_desc = "marginal — high false-signal risk"
        elif abs(ema_sep_pct) < 0.5:
            ema_sep_desc = "moderate"
        else:
            ema_sep_desc = "strong"

        # Last-candle direction.
        candle_chg_pct = (price - open_price) / open_price * 100.0 if open_price > 0 else 0.0
        candle_dir = "bullish" if candle_chg_pct >= 0 else "bearish"

        # RSI interpretation.
        rsi = g("rsi", 50.0)
        rsi_desc = "overbought" if rsi > 75 else ("oversold" if rsi < 25 else "neutral")

        # Last 5 closes, newest first (close_0 = entry candle close = price).
        closes = [g(f"close_{i}", price) for i in range(5)]
        recent_closes = ",  ".join(f"{c:.2f}" for c in closes)

        # Daily loss as fraction of the limit already consumed.
        realized_pnl = g("realized_pnl_today", 0.0)
        daily_loss_fraction = (
            -realized_pnl / equity if equity > 0 and realized_pnl < 0 else 0.0
        )

        return _USER_TEMPLATE.format(
            symbol=context.symbol,
            price=price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_sep_pct=ema_sep_pct,
            ema_sep_desc=ema_sep_desc,
            open_p=open_price,          # short key to stay under line-length limit
            candle_dir=candle_dir,
            chg=candle_chg_pct,         # short key
            vol_ratio=g("volume_ratio", 1.0),
            rsi=rsi,
            rsi_desc=rsi_desc,
            recent_closes=recent_closes,
            equity=equity,
            open_positions=int(g("open_positions", 0.0)),
            max_positions=int(g("max_open_positions", 3.0)),
            open_exposure=g("open_exposure", 0.0),
            drawdown_pct=g("drawdown_pct", 0.0),
            max_drawdown_pct=g("max_drawdown_pct", 0.10),
            pnl=realized_pnl,           # short key
            dl_frac=daily_loss_fraction,  # short key
            dl_lim=g("daily_loss_limit_pct", 0.05),  # short key
        )

    def _call(self, context: MarketContext) -> AdvisorOpinion | None:
        prompt = self._format_prompt(context)
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
