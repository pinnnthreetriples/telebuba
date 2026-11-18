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
# ``refused``: it will not be asked (``reason``). ``evidence``: the fold of passive evidence
# answered, nothing was asked (only with ``SPAM_INTEL__PASSIVE_DECISIONS``).
ServedFrom = Literal["cache", "probe", "coalesced", "evidence", "deferred", "refused"]

DeferralReason = Literal[
    "backoff",
    "account_daily_cap",
    "proxy_spacing",
    "fleet_spacing",
    "fleet_soft_cap",
    "fleet_hard_cap",
    "busy",
    "startup_quiet",
    "no_live_session",
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
    "no_live_session",
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
    # How that newest attempt ended (``started``: it never did) and the exception class if it
    # crashed or was cancelled. The coalescing window hands back a reading only when the
    # attempt that opened it produced one.
    last_attempt_outcome: AttemptOutcome | None = None
    last_attempt_error: str | None = None
    last_fleet_started_at: str | None = None  # any account
    last_proxy_started_at: str | None = None  # accounts behind this proxy
    fleet_count_24h: int = 0
    fleet_oldest_24h: str | None = None
    account_count_24h: int = 0
    account_oldest_24h: str | None = None
    operator_count_24h: int = 0


class SampleStructure(BaseModel):
    """The structural flags of the conversation a reply came from."""

    multi_message: bool = False
    has_markup: bool = False


class ReplySample(BaseModel):
    """One redacted reply of @SpamBot, kept once per distinct text."""

    text_hash: str = Field(min_length=1)
    sample: str
    lang: str | None = None
    source: Literal["probe", "history"] = "probe"
    legacy_status: str | None = None
    strict_status: str | None = None
    has_date: bool = False
    # Structural flags of the conversation the text came from (never read for a verdict yet):
    # the bot sent more than one message, and a message carried buttons or a keyboard.
    multi_message: bool = False
    has_markup: bool = False
    first_seen_at: str
    last_seen_at: str
    seen_count: int = Field(default=1, ge=1)


class ShadowAgreement(BaseModel):
    """How often the stored fold matched the real @SpamBot answer (the shadow comparison).

    ``rows`` counts each (fold status before the reading, real status) pair; ``agree`` and
    ``total`` are the sums the owner reads. Built from the ``spam_intel_shadow_comparison``
    log events, so reading it sends nothing to Telegram.
    """

    total: int = Field(default=0, ge=0)
    agree: int = Field(default=0, ge=0)
    rows: dict[str, int] = Field(default_factory=dict)
