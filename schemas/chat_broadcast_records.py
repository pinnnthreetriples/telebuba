"""Row contracts between ``core.repositories.chat_broadcast`` and ``services.chat_broadcast``.

Internal to the domain (the HTTP contracts are in ``schemas.chat_broadcast`` and
``schemas.chat_broadcast_board``): one model per table row, instants as unix seconds the
way the engine compares them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Pydantic resolves model fields at runtime, so these stay runtime imports.
from schemas.chat_broadcast import ChatBroadcastStatus  # noqa: TC001
from schemas.chat_broadcast_board import (  # noqa: TC001
    ChatBroadcastSkipReason,
    ChatBroadcastTargetKind,
    ChatBroadcastTargetState,
)

ChatBroadcastJournalStatus = Literal["pending", "sent", "failed", "unconfirmed", "deleted"]
ChatBroadcastJournalKind = Literal["text", "photo", "post"]
ChatBroadcastEventKind = Literal[
    "joined",
    "already_member",
    "requested",
    "approved",
    "captcha",
    "handed",
    "skipped",
    "reconnecting",
    "deleted",
]


class CampaignRecord(BaseModel):
    campaign_id: str
    name: str
    status: ChatBroadcastStatus
    run_id: str | None = None
    round: int = 0
    rest_until_unix: int | None = None
    started_unix: int | None = None
    resumed_unix: int | None = None
    finished_unix: int | None = None
    last_error: str | None = None
    settings_json: str = "{}"
    created_at: str
    updated_at: str
    account_count: int = 0
    target_count: int = 0


class RosterAccount(BaseModel):
    account_id: str
    position: int
    state: Literal["active", "halted"]
    halted_reason: str | None = None
    consecutive_errors: int = 0


class TargetSeed(BaseModel):
    """A chat as the run materialises it, before any account touched it."""

    chat_key: str
    raw: str
    kind: ChatBroadcastTargetKind
    title: str | None = None
    username: str | None = None
    folder_slug: str | None = None
    peer_id: int | None = None
    assigned_account_id: str | None = None
    # Set for "where it already is": the account is inside, no join is needed.
    member_account_id: str | None = None


class TargetRecord(TargetSeed):
    position: int
    handed_from_account_id: str | None = None
    state: ChatBroadcastTargetState
    skip_reason: ChatBroadcastSkipReason | None = None
    round: int
    step_index: int
    requested_unix: int | None = None
    joined_unix: int | None = None
    next_action_unix: int | None = None
    message_deleted: bool = False
    # The operator kept this chat despite deleted messages: only a ban skips it now.
    ignore_deleted: bool = False
    updated_unix: int


class JournalKey(BaseModel):
    run_id: str
    chat_key: str
    round: int
    step_index: int


class JournalRecord(JournalKey):
    id: int
    peer_id: int | None = None
    account_id: str
    kind: ChatBroadcastJournalKind
    text: str
    rewritten: bool
    tg_message_id: int | None = None
    status: ChatBroadcastJournalStatus
    error_type: str | None = None
    created_unix: int
    sent_unix: int | None = None


class EventRecord(BaseModel):
    chat_key: str
    round: int
    account_id: str | None = None
    kind: ChatBroadcastEventKind
    detail: str | None = None
    at_unix: int
