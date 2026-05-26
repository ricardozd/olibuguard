"""AI advisor interface and the default null implementation.

Safeguard (section 7): the AI can ONLY veto or shrink a trade, never start it or
enlarge it. ``clamp_advisor_factor`` materializes that invariant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from olibuguard.domain.models import MarketContext


class AdvisorOpinion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bias: float = Field(ge=-1.0, le=1.0)
    rationale: str = ""


@runtime_checkable
class AIAdvisor(Protocol):
    def opinion(self, context: MarketContext) -> AdvisorOpinion | None:
        """None if the advisor does not want to opine or is disabled."""
        ...


class NullAdvisor:
    """Default advisor: never opines."""

    def opinion(self, context: MarketContext) -> AdvisorOpinion | None:
        return None


def clamp_advisor_factor(bias: float) -> float:
    """Turn the advisor bias into a size factor in ``[0, 1]``.

    A positive bias enlarges nothing (capped at 1.0). A negative bias shrinks down
    to 0 (full veto at -1). This way the AI can never increase exposure.
    """
    if bias >= 0:
        return 1.0
    return max(0.0, 1.0 + bias)
