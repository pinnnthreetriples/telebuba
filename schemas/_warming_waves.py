"""The warming history of one account: every wave of its current life.

A wave is one warming stint; the first is wave 1 and every fresh start of the same
account life (a start from idle after a Stop, «Вернуть в прогрев») opens the next one.
Read from the lifecycle journal plus the live warming row. Data contract only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# How a wave ended: graduated (target reached or promoted), stopped by the operator,
# stopped on a confirmed spam block, the session died, the account was deleted, or —
# only for history from before waves were journalled — the next wave simply began.
WaveEndReason = Literal[
    "released", "stopped", "spam_blocked", "session_dead", "deleted", "replaced"
]


class WarmingWave(BaseModel):
    """One wave: when it began and ended, why it ended, and the days it worked."""

    wave: int = Field(ge=1)
    started_at: str
    # ``None`` while the wave is still running.
    ended_at: str | None = None
    end_reason: WaveEndReason | None = None
    # Days that counted towards the target; ``None`` when the journal never said.
    worked_days: int | None = Field(default=None, ge=0)
    target_days: int | None = Field(default=None, ge=1)
    persona: str | None = None


class WarmingWaveList(BaseModel):
    """``GET /warming/waves/{account_id}``: the account's waves, oldest first."""

    account_id: str = Field(min_length=1)
    waves: list[WarmingWave] = Field(default_factory=list)
