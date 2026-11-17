"""Contracts of the spam-intel gateway: purposes, how an answer was served, the ledger rows.

No behaviour, no I/O. ``services.spam_intel`` decides, ``core.repositories.spam_probe_ledger``
stores. See ``docs/spam-intel.md``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Why a verdict is wanted; it picks the budget class.
Purpose = Literal["routine", "decision", "measurement", "operator"]

# How an answer reached the caller. ``cache``: a stored reading inside the TTL. ``probe``: a
# real question to @SpamBot just now. ``coalesced``: another caller's reading or failure,
# younger than the coalescing window. ``deferred``: the budget says later (``retry_at``).
# ``refused``: it will not be asked (``reason``).
ServedFrom = Literal["cache", "probe", "coalesced", "deferred", "refused"]

DeferralReason = Literal[
    "backoff",
    "account_daily_cap",
    "proxy_spacing",
    "fleet_spacing",
    "fleet_soft_cap",
    "fleet_hard_cap",
    "busy",
    "startup_quiet",
]
RefusalReason = Literal[
    "account_missing",
    "account_not_alive",
    "no_proxy",
    "operator_daily_cap",
    "needs_confirmation",
]
Reason = Literal[
    "backoff",
    "account_daily_cap",
    "proxy_spacing",
    "fleet_spacing",
    "fleet_soft_cap",
    "fleet_hard_cap",
    "busy",
    "startup_quiet",
    "account_missing",
    "account_not_alive",
    "no_proxy",
    "operator_daily_cap",
    "needs_confirmation",
]

# What one ledger row records.
AttemptKind = Literal["probe", "history"]
AttemptOutcome = Literal["started", "clean", "limited", "unknown", "error", "history"]


class ProbeAttemptStart(BaseModel):
    """A real question about to go out. Written BEFORE it is asked, so a crash still counts."""

    account_id: str = Field(min_length=1)
    proxy_key: str | None = None
    kind: AttemptKind = "probe"
    purpose: Purpose
    caller: str = Field(min_length=1)
    started_at: str = Field(min_length=1)


class ProbeAttemptFinish(BaseModel):
    """How an attempt ended."""

    attempt_id: int
    finished_at: str = Field(min_length=1)
    outcome: AttemptOutcome
    error_type: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class ProbeState(BaseModel):
    """Per-account memory of the ledger: when it may be asked again, and the last failure."""

    account_id: str = Field(min_length=1)
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    fail_streak: int = Field(default=0, ge=0)
    next_allowed_at: str | None = None
    last_caller: str | None = None
    last_outcome: AttemptOutcome | None = None
    last_error: str | None = None
    history_read_at: str | None = None
    updated_at: str | None = None


class BudgetSnapshot(BaseModel):
    """Everything the budget decision reads from the ledger, in one go.

    Counts and instants are over REAL probes (``kind='probe'``); a history read counts
    toward spacing but not toward the probe caps.
    """

    state: ProbeState | None = None
    last_attempt_started_at: str | None = None  # this account, any outcome
    last_fleet_started_at: str | None = None  # any account
    last_proxy_started_at: str | None = None  # accounts behind this proxy
    fleet_count_24h: int = 0
    fleet_oldest_24h: str | None = None
    account_count_24h: int = 0
    account_oldest_24h: str | None = None
    operator_count_24h: int = 0


class ReplySample(BaseModel):
    """One redacted reply of @SpamBot, kept once per distinct text."""

    text_hash: str = Field(min_length=1)
    sample: str
    lang: str | None = None
    source: Literal["probe", "history"] = "probe"
    legacy_status: str | None = None
    strict_status: str | None = None
    has_date: bool = False
    first_seen_at: str
    last_seen_at: str
    seen_count: int = Field(default=1, ge=1)
