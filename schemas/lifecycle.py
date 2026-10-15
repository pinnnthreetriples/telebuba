"""Contracts for the account lifecycle journal (``account_lifecycle_events``).

The journal is the durable record of an account's milestones — warming start,
graduation, quarantine, @SpamBot answers, a dead session, deletion — kept past the
30-day log retention and past the account's own deletion. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# warming_started — a warming stint began (``stint_started_at`` is its start).
# warmed          — the stint reached the operator's target days.
# promoted        — graduated out of warming into the neurocomment pool.
# handed_off      — moved from the warmed card into neurocomment's idle pool.
# quarantined     — a peer flood parked the account in quarantine.
# spambot_verdict — @SpamBot answered; ``extra`` carries the status.
# session_dead    — a session check found the session revoked or the account gone.
# deleted         — the operator deleted the account; ``extra`` carries the reason.
# warming_stopped — a wave ended without a graduation: ``extra.reason`` is ``operator``
#                   (the Stop button) or ``spam_blocked`` (@SpamBot confirmed the block);
#                   ``extra`` also carries the wave and its worked days (migration 95).
LifecycleEventKind = Literal[
    "warming_started",
    "warmed",
    "promoted",
    "handed_off",
    "quarantined",
    "spambot_verdict",
    "session_dead",
    "deleted",
    "warming_stopped",
]
LIFECYCLE_EVENT_KINDS: tuple[LifecycleEventKind, ...] = (
    "warming_started",
    "warmed",
    "promoted",
    "handed_off",
    "quarantined",
    "spambot_verdict",
    "session_dead",
    "deleted",
    "warming_stopped",
)

# Why an account left the fleet: ``dead`` — Telegram took it (limited, banned, the
# session revoked); ``retired`` — the operator let a working account go.
DeleteReason = Literal["dead", "retired"]


class LifecycleEvent(BaseModel):
    """One journal row.

    ``tg_user_id`` and ``stint_started_at`` are snapshots taken at write time, so they
    stay readable after the account row is gone.
    """

    id: int = Field(ge=1)
    account_id: str = Field(min_length=1)
    tg_user_id: int | None = None
    stint_started_at: str | None = None
    kind: LifecycleEventKind
    created_at: str
    extra: dict[str, object] = Field(default_factory=dict)
    backfilled: bool = False


class LifecycleSnapshot(BaseModel):
    """What a journal row snapshots from the live account.

    A deletion takes it before the cascade and writes it only after the delete succeeded.
    """

    tg_user_id: int | None = None
    stint_started_at: str | None = None


class AccountDeleteReasonGuess(BaseModel):
    """The reason the delete dialog pre-selects; the operator may change it."""

    reason: DeleteReason
