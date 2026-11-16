"""The session guard's heartbeat, as the API serves it. No behaviour, no I/O."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Why the guard counts as stalled: its task ended outside a shutdown, or it has done
# nothing for longer than a step's deadline plus two passes.
SessionGuardStallReason = Literal["stopped", "silent"]


class SessionGuardStatus(BaseModel):
    """Whether the guard runs, when it last did something, and whether that is too long ago."""

    running: bool
    last_activity_at: str | None = None
    stalled: bool = False
    reason: SessionGuardStallReason | None = None
