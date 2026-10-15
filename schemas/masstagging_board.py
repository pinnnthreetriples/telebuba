"""Board contracts of the masstagging domain.

A sibling of :mod:`schemas.masstagging` for the file-size cap. The board is BY TARGET:
one row per person with its state, who handles it and its history. Every label is a
stable code the SPA words; no prose crosses this boundary.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # Pydantic resolves model fields at runtime.
from typing import Literal

from pydantic import BaseModel

from schemas.masstagging import (
    MasstaggingCampaign,  # noqa: TC001  # Pydantic resolves model fields at runtime.
)

MasstaggingTargetState = Literal[
    "queued",
    # The dialog is open (auto-delete on and off); the person waits for the next story.
    "primed",
    "working",
    "tagged",
    # Its account caught a flood wait; the target waits for an account.
    "flood",
    "skipped",
    "failed",
]
MasstaggingTargetReason = Literal[
    "already_tagged",
    "username_not_found",
    "phone_not_found",
    "not_a_user",
    "chat_expanded",
    "chat_hidden",
    "privacy",
    "bot",
    "deleted_account",
    "manual",
    # A story went to Telegram but no answer came back; it is never posted again.
    "unconfirmed",
    "error",
]
MasstaggingHistoryEvent = Literal[
    "dialog_created",
    "tagged",
    "flood",
    "skipped",
    "failed",
    "requeued",
    "now",
    "expanded",
]
MasstaggingAccountState = Literal[
    "working", "resting", "flood", "spamblock", "not_alive", "busy", "volume"
]
MasstaggingBusyOwner = Literal[
    "warming",
    "neurocomment",
    "neuroshilling",
    "chat_broadcast",
    "masstagging",
    "discovery",
    "user_parser",
    "bulk_messages",
]


class MasstaggingHistoryEntry(BaseModel):
    at: datetime
    account_id: str | None = None
    event: MasstaggingHistoryEvent
    # A reason code, or a count (how many were tagged in the same story).
    detail: str | None = None


class MasstaggingBoardRow(BaseModel):
    target_key: str
    target: str
    state: MasstaggingTargetState
    reason: MasstaggingTargetReason | None = None
    # When it happened (done rows) or when it is due again (a flood wait); ``None`` for
    # a target nobody has touched.
    when: datetime | None = None
    account_id: str | None = None
    # Still in work; finished, skipped and failed targets belong to the "done" tab.
    active: bool
    history: list[MasstaggingHistoryEntry]


class MasstaggingBoardAccount(BaseModel):
    account_id: str
    state: MasstaggingAccountState
    premium: bool
    busy_owner: MasstaggingBusyOwner | None = None
    # A flood pause or a rest until tomorrow ends then.
    until: datetime | None = None


class MasstaggingCounters(BaseModel):
    total: int
    tagged: int
    left: int
    failed: int
    skipped: int
    # People the working accounts can reach a day at the current pace.
    per_day: int
    # Days left at that pace; ``None`` when nothing is left or nobody can work.
    days_left: int | None = None
    working_accounts: int
    excluded_accounts: int
    free_accounts: int


class MasstaggingBoard(BaseModel):
    campaign: MasstaggingCampaign
    # The next story / dialog of a running campaign is due then.
    next_action_at: datetime | None = None
    accounts: list[MasstaggingBoardAccount]
    counters: MasstaggingCounters
    rows: list[MasstaggingBoardRow]
