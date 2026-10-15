"""The outcome of one warming cycle — split from ``schemas.warming`` for the file-size budget.

Re-exported there, so ``from schemas.warming import WarmingCycleResult`` keeps working.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CycleStatus = Literal["ok", "skipped", "flood_wait", "peer_flood", "error", "failed"]


class WarmingCycleResult(BaseModel):
    """Outcome of one warming cycle — drives counters and the activity log."""

    account_id: str = Field(min_length=1)
    status: CycleStatus
    channels_joined: int = Field(default=0, ge=0)
    channels_read: int = Field(default=0, ge=0)
    reactions_sent: int = Field(default=0, ge=0)
    messages_sent: int = Field(default=0, ge=0)
    detail: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)
    flood_wait_until: str | None = None
    failures: int = Field(default=0, ge=0)
    attempted_actions: int = Field(default=0, ge=0)
    last_failed_action: str | None = None
    last_failed_channel: str | None = None
