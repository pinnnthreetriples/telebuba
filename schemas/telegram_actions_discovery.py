"""Channel-discovery read actions — split from ``schemas.telegram_actions``.

Telegram's own channel search, reachable from an ordinary user account:

* ``SearchChannels`` → ``contacts.search`` restricted to broadcasts. Matches
  usernames *and* titles, but the server caps the result count itself and there
  is no offset parameter — breadth comes from varying the query, not paging.
* ``GetSimilarChannels`` → ``channels.getChannelRecommendations``. Given a seed
  channel it returns thematically similar public channels; with no seed it
  recommends against the account's own subscriptions. Cheap and not
  flood-prone, which makes it the preferred way to widen a keyword sweep.

Names are imported back into ``schemas.telegram_actions`` so callers keep using
``from schemas.telegram_actions import SearchChannels``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Telegram rejects global searches shorter than this ("QUERY_TOO_SHORT"), so the
# gateway short-circuits instead of spending an RPC on a guaranteed error.
CHANNEL_SEARCH_MIN_QUERY_LENGTH = 4
# Handles are at most 32 characters; discovery clamps normalization to this.
CHANNEL_HANDLE_MAX_LENGTH = 32


class SearchChannels(BaseModel):
    """Read-only: public broadcast channels matching a keyword."""

    action_type: Literal["search_channels"] = "search_channels"
    query: str = Field(min_length=1, max_length=64)
    limit: int = Field(default=20, ge=1, le=50)


class GetSimilarChannels(BaseModel):
    """Read-only: channels similar to ``seed`` (or to the account's own set)."""

    action_type: Literal["get_similar_channels"] = "get_similar_channels"
    seed: str | None = Field(default=None, max_length=CHANNEL_HANDLE_MAX_LENGTH)


class TelegramChannelMatch(BaseModel):
    """Gateway output: one public channel found by search or recommendation.

    ``participants_count`` is usually absent on search results — Telegram only
    fills it reliably in ``channels.getFullChannel``, so discovery backfills it
    later during the comments-enabled probe.
    """

    username: str = Field(min_length=1)
    title: str = ""
    participants_count: int | None = None


class TelegramChannelMatches(BaseModel):
    items: list[TelegramChannelMatch]
