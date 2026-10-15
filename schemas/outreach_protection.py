"""Account protection for outreach runs: young-account caps, rest after a limit, typing.

Shared by the saved outreach limits (the defaults) and each run request (its own
copy). Every rule is on by default and can be switched off per run.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_REST_HOURS = 168
MAX_TYPING_SECONDS = 30

# Why a run did not use an account at all: it rests after a limit, @SpamBot reports
# a spam block, or it met a flood in the last 24 hours.
SkipReason = Literal["account_resting", "account_spam_blocked", "account_recent_flood"]
# Pending sends and numbers a run dropped after too many of its accounts hit a limit.
RUN_STOPPED_CODE = "run_stopped_on_limits"


class OutreachProtection(BaseModel):
    # Accounts younger than ``young_account_days`` (registration date when the
    # operator gave one, else time in the fleet) get half of both daily caps.
    young_half_caps: bool = True
    young_account_days: int = Field(default=30, ge=1, le=365)
    # After a flood wait, peer flood or deferred imports the account rests this long;
    # later runs leave it out until then.
    rest_after_limit: bool = True
    rest_hours: int = Field(default=12, ge=1, le=MAX_REST_HOURS)
    # Once this many accounts of one run hit a limit, the whole run stops.
    stop_run_on_limits: bool = True
    stop_run_after_accounts: int = Field(default=2, ge=1, le=50)
    # Leave out accounts with a spam block or a flood in the last 24 hours.
    skip_injured: bool = True
    # Show "typing…" for a random moment in this range before each first message.
    typing: bool = True
    typing_min_seconds: float = Field(default=2, ge=0, le=MAX_TYPING_SECONDS)
    typing_max_seconds: float = Field(default=5, ge=0, le=MAX_TYPING_SECONDS)

    @model_validator(mode="after")
    def check_typing_range(self) -> OutreachProtection:
        if self.typing_max_seconds < self.typing_min_seconds:
            msg = "typing max must be at least min"
            raise ValueError(msg)
        return self


class SkippedAccount(BaseModel):
    """An account a run left out before sending anything, and for how long it would wait."""

    account_id: str
    reason: SkipReason
    # Resting only: seconds of rest left when the run started.
    retry_after_seconds: int | None = None


class OutreachRest(BaseModel):
    """The last limit an account met in outreach, and until when it rests."""

    account_id: str
    reason: str
    limited_at: str
    rest_until: str
