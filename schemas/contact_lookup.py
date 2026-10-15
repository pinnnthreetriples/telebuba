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
from schemas.outreach_limits import (
    MAX_CONTACT_IMPORTS_PER_DAY,
    MAX_LOOKUP_BATCH,
    MAX_PAUSE_SECONDS,
)
from schemas.outreach_protection import OutreachProtection, SkippedAccount  # noqa: TC001

MAX_PHONES = 1000
MAX_LOOKUP_ACCOUNTS = 20
_MAX_PHONE_LENGTH = 40


class ContactLookupRequest(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=MAX_LOOKUP_ACCOUNTS)
    phones: list[str] = Field(min_length=1, max_length=MAX_PHONES)
    # Numbers per ``ImportContacts`` call and the pause between one account's calls.
    # Left out, they come from the saved outreach limits.
    batch_size: int | None = Field(default=None, ge=1, le=MAX_LOOKUP_BATCH)
    # New numbers per account per UTC day for this run; left out, the saved limit.
    daily_cap: int | None = Field(default=None, ge=1, le=MAX_CONTACT_IMPORTS_PER_DAY)
    min_delay_seconds: float | None = Field(default=None, ge=0, le=MAX_PAUSE_SECONDS)
    max_delay_seconds: float | None = Field(default=None, ge=0, le=MAX_PAUSE_SECONDS)
    # Account protection for this run; left out, the saved defaults.
    protection: OutreachProtection | None = None

    @model_validator(mode="after")
    def validate_batch(self) -> ContactLookupRequest:
        if (self.min_delay_seconds is None) != (self.max_delay_seconds is None):
            msg = "send both delays or neither"
            raise ValueError(msg)
        if (
            self.min_delay_seconds is not None
            and self.max_delay_seconds is not None
            and self.max_delay_seconds < self.min_delay_seconds
        ):
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
    # "skipped": never checked (``error_code`` says why, ``daily_limit`` included);
    # "recheck": answered "not on Telegram" by an account that looks silently limited.
    status: Literal["found", "not_found", "skipped", "recheck"]
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None
    # Re-check row whose number went to another account: that account's row is final.
    handed_over: bool = False


class ContactLookupJob(BaseModel):
    job_id: str
    status: Literal["running", "completed", "cancelled"]
    total: int
    completed: int
    results: list[ContactLookupOutcome]
    # Accounts the run left out before checking anything, and why.
    skipped_accounts: list[SkippedAccount] = Field(default_factory=list)
