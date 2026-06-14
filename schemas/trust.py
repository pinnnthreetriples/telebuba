"""Schema for the internal account Trust Score.

This is OUR own health aggregate, not a Telegram-published metric. No behaviour
here — just the verdict produced by ``services.trust``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Bands from healthiest to worst.
TrustBand = Literal["excellent", "good", "watch", "at_risk", "critical"]


class TrustScore(BaseModel):
    """A 0-100 internal trust verdict for one account, with its band + reasons."""

    account_id: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)
    band: TrustBand
    reasons: list[str] = Field(default_factory=list)
