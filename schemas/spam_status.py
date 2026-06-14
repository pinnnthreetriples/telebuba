"""Schemas for the account spam-status probe (@SpamBot + getFullUser).

No behaviour, no I/O — the data contract between ``core.telegram_client`` (the
raw probe), ``core.db`` (the cache) and ``services.spam_status`` (parsing +
caching). See non-negotiable #2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
    """Parsed, cacheable spam-status verdict for one account."""

    account_id: str = Field(min_length=1)
    status: SpamStatusKind
    detail: str | None = None
    checked_at: str = Field(min_length=1)
