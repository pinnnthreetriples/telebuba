"""Channel-discovery search request — split from ``schemas.neurocomment_discovery``.

Its own module for the file-size cap. One-way: nothing here imports
``schemas.neurocomment_discovery``; that module imports these names back so every
``from schemas.neurocomment_discovery import DiscoverySearchRequest`` keeps working.

Field bounds are literals, not config reads: ``schemas/`` may not import ``core``
(enforced by ``tests/test_architecture.py``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.telegram_actions_discovery import DiscoveryKind  # noqa: TC001

# Telegram rejects global searches under 4 characters outright.
KEYWORD_MIN_LENGTH = 4
KEYWORD_MAX_LENGTH = 64
MAX_KEYWORDS = 10
CHANNEL_HANDLE_MAX_LENGTH = 32
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 1, 500, 200
MAX_SEARCH_ACCOUNTS = 10

# Bounded per item: the validator below measures the *stripped* form, so without this
# a single 10 MB keyword passed validation and rode into a Telegram RPC.
Keyword = Annotated[str, Field(min_length=1, max_length=KEYWORD_MAX_LENGTH)]

DiscoveryLanguage = Literal["any", "ru", "en", "uk", "other"]
DiscoveryComments = Literal["any", "on", "off"]
DiscoveryAccess = Literal["any", "open", "join_request", "subscription"]
DiscoveryCategory = Literal[
    "any",
    "it_programming",
    "beauty_health",
    "crypto",
    "trading",
    "news",
    "business",
    "marketing",
    "education",
    "entertainment",
    "games",
    "sport",
    "travel",
    "food",
    "cars",
    "real_estate",
    "finance",
    "psychology",
    "humor",
    "music",
    "movies",
    "fashion",
    "politics",
    "science",
    "parenting",
    "jobs",
]


class DiscoverySearchRequest(BaseModel):
    """Operator-supplied search parameters.

    ``members_min``/``members_max`` are applied client-side to the hits whose subscriber
    count Telegram happens to return. ``keywords`` come out stripped and deduped
    case-insensitively; a category alone (its word bundle) is enough to search on.

    ``account_ids`` is required: the SPA is the only caller and it lets the operator
    pick the searching accounts, so the server no longer auto-picks the listener.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[Keyword] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    seed_channel: str | None = Field(
        default=None,
        min_length=1,
        max_length=CHANNEL_HANDLE_MAX_LENGTH,
    )
    members_min: int | None = Field(default=None, ge=0)
    members_max: int | None = Field(default=None, ge=0)
    kind: DiscoveryKind = "channels"
    category: DiscoveryCategory = "any"
    language: DiscoveryLanguage = "any"
    comments: DiscoveryComments = "any"
    access: DiscoveryAccess = "any"
    hide_seen: bool = True
    limit: int = Field(default=LIMIT_DEFAULT, ge=LIMIT_MIN, le=LIMIT_MAX)
    account_ids: list[str] = Field(min_length=1, max_length=MAX_SEARCH_ACCOUNTS)

    @model_validator(mode="after")
    def _check_bounds(self) -> DiscoverySearchRequest:
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            stripped = keyword.strip()
            if not (KEYWORD_MIN_LENGTH <= len(stripped) <= KEYWORD_MAX_LENGTH):
                msg = (
                    f"each keyword must be {KEYWORD_MIN_LENGTH}-{KEYWORD_MAX_LENGTH} "
                    "characters (Telegram rejects shorter global searches)"
                )
                raise ValueError(msg)
            if stripped.casefold() in seen:
                continue
            seen.add(stripped.casefold())
            deduped.append(stripped)
        self.keywords = deduped
        if not self.keywords and self.category == "any":
            msg = "keywords or a category is required"
            raise ValueError(msg)
        if self.seed_channel is not None and not self.seed_channel.strip():
            # A blank seed is truthy, so it survived into a pace sleep and a peer
            # resolution and yielded nothing. The rest of the normalization is the
            # service's (``core.channel_tokens`` is off limits to ``schemas/``).
            msg = "seed_channel must not be blank"
            raise ValueError(msg)
        if (
            self.members_min is not None
            and self.members_max is not None
            and self.members_min > self.members_max
        ):
            msg = "members_min must not exceed members_max"
            raise ValueError(msg)
        if self.kind == "groups" and self.comments != "any":
            msg = "groups have no comment verdict; comments must be 'any'"
            raise ValueError(msg)
        if self.kind == "groups" and self.access == "subscription":
            msg = "recommendations return channels only; groups cannot filter by subscription"
            raise ValueError(msg)
        accounts = [account_id.strip() for account_id in self.account_ids]
        self.account_ids = list(dict.fromkeys(account_id for account_id in accounts if account_id))
        if not self.account_ids:
            msg = "account_ids must name at least one account"
            raise ValueError(msg)
        return self


# Why a listed account cannot search right now. ``account_busy`` = its session is taken
# (listener, warming, another run); ``account_cooling`` = Telegram is rate-limiting it;
# ``no_session`` = it has never been signed in on this dashboard.
DiscoveryBusyReason = Literal["account_busy", "account_cooling", "no_session"]


class DiscoveryAccountOption(BaseModel):
    """One account the operator may pick for a search."""

    account_id: str
    name: str
    premium: bool | None = None
    busy_reason: DiscoveryBusyReason | None = None


class DiscoveryAccountList(BaseModel):
    items: list[DiscoveryAccountOption]
