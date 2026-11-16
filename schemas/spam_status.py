"""Schemas for the account spam-status probe (@SpamBot + getFullUser).

No behaviour, no I/O — the data contract between ``core.telegram_client`` (the
raw probe), ``core.db`` (the cache) and ``services.spam_status`` (parsing +
caching). See non-negotiable #2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.spam_intel import Reason, ServedFrom  # noqa: TC001 - pydantic field types.

# clean   — @SpamBot reports no limits.
# limited — account is spam-restricted (write to contacts / repliers only).
# unknown — could not determine (probe failed, ambiguous reply, "being checked").
SpamStatusKind = Literal["clean", "limited", "unknown"]


class SpamStatusProbe(BaseModel):
    """Raw result of the gateway probe — unparsed bot text + restriction flags."""

    account_id: str = Field(min_length=1)
    reply_text: str | None = None
    restricted: bool = False
    restriction_reason: str | None = None
    error: str | None = None


class SpamStatusVerdict(BaseModel):
    """Parsed, cacheable spam-status verdict for one account.

    The last three fields are never stored: the gateway fills them on an answer so the
    caller can tell "not asked, the budget says later" (``deferred``, ``retry_at``) and
    "will not be asked" (``refused``, ``reason``) from a reading that came back
    unreadable. ``None`` everywhere means a plain stored reading, as before the gateway.
    """

    account_id: str = Field(min_length=1)
    status: SpamStatusKind
    detail: str | None = None
    checked_at: str = Field(min_length=1)
    served_from: ServedFrom | None = None
    retry_at: str | None = None
    reason: Reason | None = None


def is_unasked(verdict: SpamStatusVerdict) -> bool:
    """True for an answer that carries no reading because nothing was asked (budget)."""
    return verdict.served_from in {"deferred", "refused"}


class ProbeOutcome(BaseModel):
    """What one real probe produced: the verdict, and the exception class if it failed.

    ``error`` set means the question never produced a reading (the verdict is then an
    uncached ``unknown`` for the caller only).
    """

    verdict: SpamStatusVerdict
    error: str | None = None


class SpamBotHistory(BaseModel):
    """What was already in an account's @SpamBot dialog: the bot's own messages, newest first.

    Read-only (``messages.getHistory``); nothing was sent. ``error`` is an exception class
    name when the read failed.
    """

    account_id: str = Field(min_length=1)
    texts: list[str] = Field(default_factory=list)
    error: str | None = None
