"""Interfaz del AI advisor y la implementación nula por defecto.

Salvaguarda (sección 7): la IA SOLO puede vetar o reducir una operación, nunca
iniciarla ni agrandarla. ``clamp_advisor_factor`` materializa esa invariante.
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
        """None si el advisor no quiere opinar o está deshabilitado."""
        ...


class NullAdvisor:
    """Advisor por defecto: nunca opina."""

    def opinion(self, context: MarketContext) -> AdvisorOpinion | None:
        return None


def clamp_advisor_factor(bias: float) -> float:
    """Convierte el bias del advisor en un factor de tamaño en ``[0, 1]``.

    Bias positivo no agranda nada (tope 1.0). Bias negativo reduce hasta 0
    (veto total en -1). Así la IA jamás puede aumentar la exposición.
    """
    if bias >= 0:
        return 1.0
    return max(0.0, 1.0 + bias)
