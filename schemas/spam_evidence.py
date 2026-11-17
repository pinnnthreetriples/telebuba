"""Contracts of spam evidence and of the verdict folded from it (docs/spam-intel.md, 5.3-5.4).

No behaviour, no I/O. ``services.spam_intel`` records and folds, ``core.repositories.
spam_evidence`` stores. Weights, half-lives and expiries are policy in code
(``services.spam_intel._evidence_policy``), never stored in a row: changing a weight
re-folds history without a migration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from schemas.spam_status import SpamStatusVerdict  # noqa: TC001 - pydantic field type.

# What was observed (design table 5.3). ``tos_cleared`` is the closer of ``tos_restricted``
# the way ``freeze_cleared`` closes ``freeze_seen``.
EvidenceKind = Literal[
    "spambot_limited",
    "spambot_clean",
    "spambot_unreadable",
    "spambot_reviewing",
    "peer_flood_send",
    "peer_flood_other",
    "user_banned_in_channel",
    "tos_restricted",
    "tos_cleared",
    "freeze_seen",
    "freeze_cleared",
    "dm_stranger_ok",
    "group_send_ok",
    "join_ok",
    "read_ok",
    "dm_known_peer_ok",
    "flood_wait",
]

# Which way a kind points. ``none``: it moves no verdict (a restriction flag, a closer, a
# shadow success, a rate limit).
EvidenceDirection = Literal["limited", "clean", "frozen", "none"]

# The folded verdict. ``frozen`` is a verdict of the fold only: the legacy ``status`` column
# keeps ``clean`` / ``limited`` / ``unknown`` (``SpamStatusKind``).
VerdictStatus = Literal["clean", "limited", "unknown", "frozen"]
ConfidenceBucket = Literal["high", "medium", "low"]

# Why a verdict is what it is, when the status alone does not say.
#   contradiction: ``limited`` although a newer, weaker clean item exists (ask @SpamBot).
#   limit_expired: a dated limit ran out and nothing else decides (ask @SpamBot).
#   recheck_due:   a limit without a date is older than ``limited_recheck_days``.
#   stale:         ``clean`` whose strength fell below one half.
#   reviewing / unreadable: the last @SpamBot answer carried no verdict.
#   frozen:        the account is frozen.
#   no_evidence:   nothing has been recorded.
VerdictReason = Literal[
    "contradiction",
    "limit_expired",
    "recheck_due",
    "stale",
    "reviewing",
    "unreadable",
    "frozen",
    "no_evidence",
]

# A kind that records a STATE (on until its closer arrives) rather than an event. For these
# the repository coalesces across time while no opposite item came in between, and pruning
# keeps the newest item of each.
STATE_KIND_CLOSER: dict[EvidenceKind, EvidenceKind] = {
    "freeze_seen": "freeze_cleared",
    "freeze_cleared": "freeze_seen",
    "tos_restricted": "tos_cleared",
    "tos_cleared": "tos_restricted",
}

DETAIL_MAX_CHARS = 200


def _utc(value: datetime) -> datetime:
    """A naive instant is read as UTC (design risk R11)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Evidence(BaseModel):
    """One observation about one account: a fact with a time and a source, no weight."""

    account_id: str = Field(min_length=1)
    kind: EvidenceKind
    direction: EvidenceDirection
    observed_at: datetime
    expires_at: datetime | None = None
    source: str = Field(min_length=1, max_length=64)
    operation: str | None = Field(default=None, max_length=64)
    # A short, redacted fact (an error class, a date, a reason) — never a quoted third-party text.
    detail: str | None = Field(default=None, max_length=DETAIL_MAX_CHARS)
    # How many sightings the row stands for (coalescing); ``last_seen_at`` is the latest.
    count: int = Field(default=1, ge=1)
    last_seen_at: datetime | None = None

    @field_validator("observed_at", "expires_at", "last_seen_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def _seen(self) -> Evidence:
        if self.last_seen_at is None:
            self.last_seen_at = self.observed_at
        return self


class Verdict(BaseModel):
    """What the evidence says about an account's spam status, at one instant.

    ``status`` is the fold's verdict (it may be ``frozen``); the legacy ``account_spam_status.
    status`` is a different column that only a real @SpamBot answer writes unless
    ``SPAM_INTEL__PASSIVE_DECISIONS`` is on. ``tos_restricted`` is a flag beside the verdict:
    a platform restriction that never moves it (Telegram does not tie it to spam limits).
    ``weights_used`` maps each kind seen to the base weight the fold applied; it is empty on
    a verdict read back from the stored projection.
    """

    status: VerdictStatus
    confidence: ConfidenceBucket = "low"
    valid_until: datetime | None = None
    primary_kind: EvidenceKind | None = None
    # When the deciding observation (``primary_kind``) was made; ``None`` for a projection folded
    # before the column existed (migration 119) and for a verdict with no deciding item.
    primary_observed_at: datetime | None = None
    last_spambot_at: datetime | None = None
    needs_probe: bool = False
    contradiction: bool = False
    reason: VerdictReason | None = None
    tos_restricted: bool = False
    tos_reason: str | None = None
    weights_used: dict[EvidenceKind, float] = Field(default_factory=dict)

    @field_validator("valid_until", "last_spambot_at", "primary_observed_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)


class StoredProjection(BaseModel):
    """What ``account_spam_status`` holds for one account.

    ``verdict``: the folded projection, or ``None`` before the first fold. ``legacy``: the
    reading the pre-evidence code stores (``status``, ``detail``, ``checked_at``), or ``None``
    when the row exists only to carry the projection. ``folded_at``: when the fold ran.
    """

    account_id: str = Field(min_length=1)
    verdict: Verdict | None = None
    folded_at: datetime | None = None
    legacy: SpamStatusVerdict | None = None
