"""Board, journal and target contracts of the chat-broadcast domain.

A sibling of :mod:`schemas.chat_broadcast` for the file-size cap. The board is BY CHAT:
one row per target with who serves it, how far the chain got and its history. Every
label is a stable code the SPA words; no prose crosses this boundary.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # Pydantic resolves model fields at runtime.
from typing import Literal

from pydantic import BaseModel

from schemas.chat_broadcast import (
    ChatBroadcastCampaign,  # noqa: TC001  # Pydantic resolves model fields at runtime.
)

ChatBroadcastTargetKind = Literal["public", "invite", "folder", "own"]
ChatBroadcastTargetState = Literal[
    "queued",
    "joining",
    "captcha",
    "pending_approval",
    # Joined; the first message is due at ``next_action_at`` (the pause after a join).
    "waiting",
    "writing",
    "reconnecting",
    # Its account dropped out and no live account could take it yet.
    "waiting_account",
    # This round's chain is complete; the chat waits for the next round.
    "round_done",
    "done",
    "skipped",
]
ChatBroadcastSkipReason = Literal[
    "rejected",
    "not_approved",
    "admin_only",
    "deleted",
    "banned",
    "captcha",
    "manual",
    "error",
    "invalid_link",
    "unreachable",
    "already_written",
    "removed",
]
# Where a campaign is, as the pipeline shows it. ``joining`` and ``resting`` are phases
# of a running campaign, derived from its chats and its rest window.
ChatBroadcastPhase = Literal[
    "draft",
    "joining",
    "running",
    "resting",
    "stopping",
    "stopped",
    "done",
    "failed",
    "stalled",
]
ChatBroadcastHistoryKind = Literal[
    "sent",
    "unconfirmed",
    "failed",
    "deleted",
    "joined",
    "already_member",
    "requested",
    "approved",
    "captcha",
    "handed",
    "skipped",
    "reconnecting",
]
ChatBroadcastBusyOwner = Literal[
    "warming", "neurocomment", "neuroshilling", "chat_broadcast", "discovery", "user_parser"
]


class ChatBroadcastHistoryEntry(BaseModel):
    at: datetime
    account_id: str | None = None
    round: int
    kind: ChatBroadcastHistoryKind
    # The message as it went out, for ``sent``/``deleted``/``unconfirmed`` entries.
    text: str | None = None
    # A code or a number: the skip reason, the account a chat came from, a due time.
    detail: str | None = None


class ChatBroadcastBoardRow(BaseModel):
    chat_key: str
    raw: str
    title: str | None = None
    kind: ChatBroadcastTargetKind
    account_id: str | None = None
    handed_from: str | None = None
    state: ChatBroadcastTargetState
    skip_reason: ChatBroadcastSkipReason | None = None
    round: int
    # Messages this chat got in the current run, and how many it will get in all
    # (chain length x rounds; ``None`` when the rounds are endless).
    sent_total: int
    planned_total: int | None = None
    next_action_at: datetime | None = None
    requested_at: datetime | None = None
    last_text: str | None = None
    last_sent_at: datetime | None = None
    message_deleted: bool = False
    # The operator kept the chat despite deleted messages; only a ban skips it now.
    ignore_deleted: bool = False
    # Still being worked; finished and skipped chats belong to the "done" tab.
    active: bool
    history: list[ChatBroadcastHistoryEntry]


class ChatBroadcastBoardAccount(BaseModel):
    account_id: str
    state: Literal["active", "halted", "busy"]
    halted_reason: str | None = None
    busy_owner: ChatBroadcastBusyOwner | None = None


class ChatBroadcastCounters(BaseModel):
    accounts: int
    accounts_working: int
    chats: int
    joined: int
    pending_approval: int
    waiting: int
    sent: int
    skipped: int
    handed: int
    # Messages the whole campaign is meant to send; ``None`` when it is endless.
    planned: int | None = None
    rounds: int | None = None


class ChatBroadcastBoard(BaseModel):
    campaign: ChatBroadcastCampaign
    phase: ChatBroadcastPhase
    chain_length: int
    started_at: datetime | None = None
    resumed_at: datetime | None = None
    finished_at: datetime | None = None
    accounts: list[ChatBroadcastBoardAccount]
    counters: ChatBroadcastCounters
    rows: list[ChatBroadcastBoardRow]


class ChatBroadcastOwnGroup(BaseModel):
    """A group selected accounts are in; ``peer_id`` is a decimal string (int64)."""

    peer_id: str
    title: str
    username: str | None = None
    account_ids: list[str]


class ChatBroadcastOwnChats(BaseModel):
    groups: list[ChatBroadcastOwnGroup]
    channels_skipped: int
    admin_only_skipped: int
    # Accounts whose dialogs could not be read right now (offline, flood wait).
    unavailable_account_ids: list[str]


class ChatBroadcastResolvedTarget(BaseModel):
    raw: str
    key: str | None = None
    kind: ChatBroadcastTargetKind | None = None
    error: Literal["invalid_target", "folder_unavailable"] | None = None
    folder_title: str | None = None
    folder_count: int | None = None


class ChatBroadcastResolved(BaseModel):
    items: list[ChatBroadcastResolvedTarget]
