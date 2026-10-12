"""Flood memory: the lifetime record of an account's spam blocks.

Data contract only. One ``warming_flood_events`` row per PeerFlood the warming
loop met; the trust score and the account card read the per-account summary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Only PeerFlood (a spam block) is remembered today; a plain FloodWait is a timed
# cooldown, not a verdict on the account.
WarmingFloodKind = Literal["peer_flood"]


class WarmingFloodEventWrite(BaseModel):
    """One spam block to remember for an account."""

    account_id: str = Field(min_length=1)
    kind: WarmingFloodKind = "peer_flood"
    created_at: str = Field(min_length=1)
    # The action that met the block, when known (diagnostic only).
    action: str | None = None


class WarmingFloodSummary(BaseModel):
    """How many spam blocks an account has met, and when the latest one was."""

    flood_total: int = Field(default=0, ge=0)
    last_flood_at: str | None = None
