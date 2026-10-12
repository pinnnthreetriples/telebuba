"""Per-account, per-day warming totals — data contract only, no behaviour or I/O.

One row adds up every cycle of one account on one day, so trends stay readable for a
year after the activity log itself has been purged. ``phase`` and the trust fields
are the last value seen that day, not a sum; they are ``None`` on rows backfilled
from the activity log, which never carried them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.trust import TrustBand  # noqa: TC001 - pydantic resolves it at runtime.
from schemas.warming import WarmingPhase  # noqa: TC001 - pydantic resolves it at runtime.


class WarmingDailyStats(BaseModel):
    """One account's warming totals for one day (also the increment one cycle adds)."""

    account_id: str = Field(min_length=1)
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cycles: int = Field(default=0, ge=0)
    reads: int = Field(default=0, ge=0)
    reactions: int = Field(default=0, ge=0)
    joins: int = Field(default=0, ge=0)
    dms: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    floods: int = Field(default=0, ge=0)
    phase: WarmingPhase | None = None
    trust_score: int | None = Field(default=None, ge=0, le=100)
    trust_band: TrustBand | None = None


class WarmingDailyStatsList(BaseModel):
    """Daily rows of one account, oldest day first."""

    items: list[WarmingDailyStats] = Field(default_factory=list)
