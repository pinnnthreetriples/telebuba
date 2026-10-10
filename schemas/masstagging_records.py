"""Row contracts between ``core.repositories.masstagging`` and ``services.masstagging``.

Internal to the domain (the HTTP contracts are in ``schemas.masstagging`` and
``schemas.masstagging_board``): one model per table row, instants as unix seconds the
way the engine compares them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Pydantic resolves model fields at runtime, so these stay runtime imports.
from schemas.masstagging import MasstaggingMode  # noqa: TC001
from schemas.masstagging_board import (  # noqa: TC001
    MasstaggingHistoryEvent,
    MasstaggingTargetReason,
    MasstaggingTargetState,
)

# What the run itself is doing; ``draft``/``ready``/``resting`` are derived from it.
MasstaggingRunStatus = Literal["idle", "running", "stopping", "paused", "done", "failed"]
# How a target was written: a username, a phone, a chat link (its members are the
# targets), or a contact of one account (only that account can reach it by id).
MasstaggingTargetKind = Literal["username", "phone", "link", "contact"]


class CampaignRecord(BaseModel):
    campaign_id: str
    name: str
    mode: MasstaggingMode
    run_status: MasstaggingRunStatus
    run_id: str | None = None
    rest_until_unix: int | None = None
    started_unix: int | None = None
    # The last Start or Continue: "stop after N" counts from here.
    session_unix: int | None = None
    finished_unix: int | None = None
    last_error: str | None = None
    settings_json: str = "{}"
    created_at: str
    updated_at: str
    account_count: int = 0
    target_count: int = 0


class TargetSeed(BaseModel):
    """A target as the run lays it out, before any account touched it."""

    target_key: str
    raw: str
    kind: MasstaggingTargetKind
    # Set for a contact (only its account knows the person) and for a chat's members.
    assigned_account_id: str | None = None
    resolved_user_id: int | None = None
    username: str | None = None
    first_name: str = ""


class TargetRecord(TargetSeed):
    position: int
    state: MasstaggingTargetState
    reason: MasstaggingTargetReason | None = None
    account_id: str | None = None
    # The account whose session resolved ``resolved_user_id``; an id is per account.
    resolved_by: str | None = None
    next_action_unix: int | None = None
    story_id: int | None = None
    updated_unix: int


class JournalRecord(BaseModel):
    target_key: str
    event: MasstaggingHistoryEvent
    at_unix: int
    account_id: str | None = None
    detail: str | None = None


class AccountDay(BaseModel):
    """One account's counters for one masstagging day (it starts at 09:00 local)."""

    account_id: str
    day: str
    stories: int = 0
    dialogs: int = 0
    tags: int = 0
    resolves: int = 0
    floods_in_row: int = 0
    paused_until_unix: int | None = None
