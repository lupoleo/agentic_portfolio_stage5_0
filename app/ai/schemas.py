from __future__ import annotations

from enum import Enum

from pydantic import Field

from .models import AIModel


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class FinancialSentimentResult(AIModel):
    """Validated output for a basic financial sentiment classification."""

    sentiment: SentimentLabel
    confidence: float = Field(ge=0.0, le=1.0)
    requires_deep_research: bool
