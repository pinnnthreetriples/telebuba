"""The channel-pool search: «Найти каналы», the automatic refill and its consent (4.5+).

Split from ``schemas.warming`` for the file-size budget. Data contract only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

_Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)]

# ``ok``: searched, and every fit went into the pool.
# ``no_search_account``: no account may search — none is free and none can be pulled.
# ``needs_consent``: no account is free; ``pull_candidate`` names the busy one the
#   search would take, and nothing happens until the operator agrees.
# ``no_topics``: nothing to search for (no topics given, none known for the language).
# ``already_running``: another pool search is reading right now.
# ``flooded``: Telegram rate-limited the account mid-search; what fitted so far is kept.
# ``failed``: the search ran out of time.
PoolFillStatus = Literal[
    "ok",
    "no_search_account",
    "needs_consent",
    "no_topics",
    "already_running",
    "flooded",
    "failed",
]
# What a busy account is doing, as the consent dialog names it.
PoolSearchBusyWith = Literal["neurocomment", "neuroshilling", "chat_broadcast", "masstagging"]
# Who asked for a search: a warming account for itself, the automatic refill, the operator.
ChannelSearchOrigin = Literal["self", "refill", "manual"]
ChannelSearchStatus = Literal["ok", "flood", "failed", "no_account"]


class PoolFillRequest(BaseModel):
    """«Найти каналы»: what to look for, in which language and country.

    Without topics the search takes them from the interests of that language's
    accounts. ``pull_account_id`` is the operator's consent to take that busy account
    off its work for the search; it is checked again before anything is taken.
    """

    language: str = Field(pattern=r"^[a-z]{2}$")
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    topics: list[_Topic] = Field(default_factory=list, max_length=5)
    pull_account_id: str | None = Field(default=None, min_length=1, max_length=200)
    # What the operator was told it is doing; a pull is refused if that has changed.
    pull_busy_with: PoolSearchBusyWith | None = None


class PoolSearchCandidate(BaseModel):
    """The busy account a search would take, and what it would stop doing for it."""

    account_id: str
    name: str
    busy_with: PoolSearchBusyWith


class PoolFillResult(BaseModel):
    """How a search went: hits seen, channels checked, channels added to the pool."""

    status: PoolFillStatus
    found: int = Field(default=0, ge=0)
    checked: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    pull_candidate: PoolSearchCandidate | None = None


class PoolRefillRequest(BaseModel):
    """The automatic refill wanted to search this language and found no free account."""

    language: str = Field(pattern=r"^[a-z]{2}$")
    requested_at: str


class ChannelSearchRecord(BaseModel):
    """One search, as the history that spaces the next ones keeps it."""

    origin: ChannelSearchOrigin
    account_id: str | None = None
    language: str
    status: ChannelSearchStatus
    found: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    created_at: str
