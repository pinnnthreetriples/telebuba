"""Channel-discovery read actions — split from ``schemas.telegram_actions``.

Telegram's own channel search, reachable from an ordinary user account:

* ``SearchChannels`` → ``contacts.search`` restricted to broadcasts. Matches
  usernames *and* titles, but the server caps the result count itself and there
  is no offset parameter — breadth comes from varying the query, not paging.
* ``GetSimilarChannels`` → ``channels.getChannelRecommendations``. Given a seed
  channel it returns thematically similar public channels; with no seed it
  recommends against the account's own subscriptions. Cheap and not
  flood-prone, which makes it the preferred way to widen a keyword sweep.
* ``SearchGlobalPosts`` → ``messages.searchGlobal`` restricted to broadcasts. A
  different index from the two above: it matches what a channel POSTS, not what
  it is called, so it finds channels whose title never carries the keyword. It
  is the only one of the three that pages, via an opaque cursor.

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


class GlobalPostsCursor(BaseModel):
    """Where the previous ``SearchGlobalPosts`` page stopped.

    Telegram pages this search by three values taken from the previous reply, so
    they travel together or not at all. ``peer`` is the *handle* of the channel
    that posted the last message of that page, not an ``InputPeer``: an action
    crosses a typed boundary and the gateway re-resolves the handle. A page whose
    last message came from a channel with no public handle carries ``None`` here,
    which is what Telethon's own global-search iterator sends too.
    """

    offset_rate: int = Field(default=0, ge=0)
    peer: str | None = Field(default=None, max_length=CHANNEL_HANDLE_MAX_LENGTH)
    offset_id: int = Field(default=0, ge=0)


class SearchGlobalPosts(BaseModel):
    """Read-only: public broadcast channels whose POSTS match a keyword.

    ``limit`` counts messages, not channels — several matching posts usually come
    from the same channel, so one page yields fewer channels than its limit.
    """

    action_type: Literal["search_global_posts"] = "search_global_posts"
    query: str = Field(min_length=1, max_length=64)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: GlobalPostsCursor | None = None


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


class TelegramGlobalPostMatches(TelegramChannelMatches):
    """``TelegramChannelMatches`` plus where to continue the post search.

    A subclass so every caller that already accepts ``TelegramChannelMatches``
    keeps working unchanged; only a caller that wants page two reads the cursor.
    ``next_cursor`` is ``None`` when the page carried no message to continue from.
    """

    next_cursor: GlobalPostsCursor | None = None
