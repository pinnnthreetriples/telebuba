"""Contracts for an operator-started contact-lookup run (phones -> Telegram users).

Mirror of ``schemas.bulk_messages``: the operator uploads phone numbers, the run
resolves which of them are on Telegram (across the selected accounts, in batches),
and the found users feed straight into a bulk-message run as recipients.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from schemas.accounts import _ACCOUNT_ID_PATTERN

MAX_PHONES = 1000
MAX_LOOKUP_ACCOUNTS = 20
_MAX_PHONE_LENGTH = 40


class ContactLookupRequest(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=MAX_LOOKUP_ACCOUNTS)
    phones: list[str] = Field(min_length=1, max_length=MAX_PHONES)
    min_delay_seconds: float = Field(default=0, ge=0, le=300)
    max_delay_seconds: float = Field(default=0, ge=0, le=300)

    @model_validator(mode="after")
    def validate_batch(self) -> ContactLookupRequest:
        if self.max_delay_seconds < self.min_delay_seconds:
            msg = "max_delay_seconds must be at least min_delay_seconds"
            raise ValueError(msg)
        if len(self.account_ids) != len(set(self.account_ids)):
            msg = "duplicate account_ids"
            raise ValueError(msg)
        if any(
            not re.fullmatch(_ACCOUNT_ID_PATTERN, account_id) for account_id in self.account_ids
        ):
            msg = "invalid account_id"
            raise ValueError(msg)
        if any(not phone.strip() or len(phone) > _MAX_PHONE_LENGTH for phone in self.phones):
            msg = "invalid phone"
            raise ValueError(msg)
        return self


class ContactLookupOutcome(BaseModel):
    phone: str
    account_id: str
    status: Literal["found", "not_found", "skipped"]
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None


class ContactLookupJob(BaseModel):
    job_id: str
    status: Literal["running", "completed", "cancelled"]
    total: int
    completed: int
    results: list[ContactLookupOutcome]
