"""Contracts for the lead queue: phone numbers the operator adds over the day.

Each queued lead is looked up on Telegram within the working window and, when
found, gets the queue's first message from the account that found it.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from schemas.accounts import _ACCOUNT_ID_PATTERN
from schemas.bulk_runs import WaitReason  # noqa: TC001

# "pending": waiting for a lookup; "found": waiting for a send; "messaging": in a run;
# "skipped": the client was already contacted (queued under another number, or the
# recipient-history rules refused the send).
LeadStatus = Literal[
    "pending", "found", "messaging", "sent", "not_found", "failed", "unconfirmed", "skipped"
]
MAX_LEADS_PER_ADD = 1000
MAX_QUEUE_ACCOUNTS = 20
MAX_LISTED_LEADS = 1000
_MAX_PHONE_LENGTH = 40
_MAX_NAME_LENGTH = 100


class LeadInput(BaseModel):
    phone: str = Field(min_length=1, max_length=_MAX_PHONE_LENGTH)
    # The client's name as the operator typed it; shown in the queue only.
    name: str | None = Field(default=None, max_length=_MAX_NAME_LENGTH)


class LeadQueueAddRequest(BaseModel):
    leads: list[LeadInput] = Field(min_length=1, max_length=MAX_LEADS_PER_ADD)


class LeadQueueAddResult(BaseModel):
    added: int
    # Already in the queue (any status): a number is never queued twice.
    duplicates: int
    invalid: int


class LeadQueueItem(BaseModel):
    id: int | None = None
    phone: str
    # ``phone`` without its leading "+": what makes two spellings one lead.
    phone_key: str
    client_name: str | None = None
    status: LeadStatus = "pending"
    # The account that looked the number up; it alone may write to the user it found.
    account_id: str | None = None
    user_id: int | None = None
    # An account whose "not on Telegram" looked silently limited: another one re-checks.
    recheck_account_id: str | None = None
    run_id: str | None = None
    error_code: str | None = None
    created_at: str
    looked_up_at: str | None = None
    finished_at: str | None = None


class LeadQueueSettingsUpdate(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=MAX_QUEUE_ACCOUNTS)
    text: str = Field(default="", max_length=4096)
    paused: bool = False

    @model_validator(mode="after")
    def validate_settings(self) -> LeadQueueSettingsUpdate:
        if len(self.account_ids) != len(set(self.account_ids)):
            msg = "duplicate account_ids"
            raise ValueError(msg)
        if any(not re.fullmatch(_ACCOUNT_ID_PATTERN, a) for a in self.account_ids):
            msg = "invalid account_id"
            raise ValueError(msg)
        if not self.paused and (not self.account_ids or not self.text.strip()):
            msg = "a running queue needs accounts and a message"
            raise ValueError(msg)
        return self


class LeadQueueSettings(BaseModel):
    account_ids: list[str] = Field(default_factory=list)
    text: str = ""
    # Nothing is looked up or sent until the operator saves accounts and a message.
    paused: bool = True
    # Who saved the settings last: the queue's runs are theirs.
    owner_user_id: str | None = None


class LeadQueueView(BaseModel):
    settings: LeadQueueSettings
    # Leads per status, over the whole queue.
    counts: dict[str, int]
    # The newest leads first.
    items: list[LeadQueueItem]
    # The queue's live send run: why it is idle and until when.
    wait_reason: WaitReason | None = None
    next_send_at: str | None = None
