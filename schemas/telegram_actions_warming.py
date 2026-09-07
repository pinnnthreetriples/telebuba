"""Warming *extras* actions — the side actions a real client performs between reads.

Sibling of ``schemas.telegram_actions`` (file-size cap); every class here is
re-exported from it and joins ``TelegramAction``. All ``action_type`` values carry
the ``warm_`` prefix: the write dispatcher routes the whole family through one
prefix-matched arm, and the gateway log rows come out as
``warming_telegram_warm_<...>``.

Reads and writes both ride the write executor deliberately: it is the only path
with the flood ladder, the ``ActionResult`` contract and free per-action log rows,
and warming never consumes the returned data. Whether a cycle books daily budget
for an action is the service's decision, not encoded here.

Contract rule: no model carries a destination peer for a self-scoped write —
``InputPeerSelf`` is hardcoded in ``core`` so a caller cannot aim a note, forward,
draft or reminder at another account.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class WarmGetDialogs(BaseModel):
    """Read: open the chat list, like launching the app (``messages.getDialogs``)."""

    action_type: Literal["warm_get_dialogs"] = "warm_get_dialogs"
    limit: int = Field(default=30, ge=1, le=100)


class WarmReadContacts(BaseModel):
    """Read-only contact sync (``contacts.getContacts`` + ``getStatuses``); never imports."""

    action_type: Literal["warm_read_contacts"] = "warm_read_contacts"


class WarmReadNotifySettings(BaseModel):
    """Read the three global notification scopes (broadcasts / users / chats)."""

    action_type: Literal["warm_read_notify_settings"] = "warm_read_notify_settings"


class WarmCheckSettings(BaseModel):
    """Read a random bundle of ``calls`` settings screens (privacy, sessions, app config...)."""

    action_type: Literal["warm_check_settings"] = "warm_check_settings"
    calls: int = Field(default=3, ge=1, le=7)
    # Where in the 11-entry read table the bundle starts. Randomness lives in the
    # service; core stays deterministic and only rotates from here.
    offset: int = Field(default=0, ge=0, le=10)


class WarmViewProfile(BaseModel):
    """Read one profile: our own (``kind="self"``) or a channel we read (``kind="channel"``).

    DM peers and comment authors are deliberately not addressable — viewing them
    would tie pool accounts together.
    """

    action_type: Literal["warm_view_profile"] = "warm_view_profile"
    kind: Literal["self", "channel", "bot"] = "self"
    channel: str | None = None
    # ``kind="bot"``: username of a whitelisted official inline bot (see ``WarmInlineQuery``).
    bot: str | None = None


# Official Telegram inline bots a warming account may query. Anything else is refused
# before any RPC: an arbitrary bot is a third party that sees the query.
INLINE_BOT_WHITELIST: frozenset[str] = frozenset(
    {"gif", "pic", "vid", "wiki", "bing", "youtube", "bold"}
)


class WarmSearchMessages(BaseModel):
    """Read: search inside a channel for a word taken from a post the account just read.

    Core fetches ``message_ids`` (own read), picks one alphabetic word of four or more
    letters, and issues ``messages.search``; with no usable word it falls back to
    ``fallback_query``. ``global_search`` adds one ``messages.searchGlobal`` — the
    flood-sensitive half, so the service enables it rarely.
    """

    action_type: Literal["warm_search_messages"] = "warm_search_messages"
    channel: str
    message_ids: list[int] = Field(min_length=1, max_length=5)
    fallback_query: str = Field(min_length=2, max_length=32)
    global_search: bool = False


class WarmLinkPreview(BaseModel):
    """Read: open the web-page preview of the first http(s) link in a post just read."""

    action_type: Literal["warm_link_preview"] = "warm_link_preview"
    channel: str
    message_ids: list[int] = Field(min_length=1, max_length=5)


class WarmBrowseStickers(BaseModel):
    """Read: open the sticker panel (installed, recent, featured) and one featured set."""

    action_type: Literal["warm_browse_stickers"] = "warm_browse_stickers"


class WarmSavedGifs(BaseModel):
    """Read: open the GIF tab (``messages.getSavedGifs``)."""

    action_type: Literal["warm_saved_gifs"] = "warm_saved_gifs"


class WarmInlineQuery(BaseModel):
    """Read: ask a whitelisted inline bot for results; the result is never sent anywhere."""

    action_type: Literal["warm_inline_query"] = "warm_inline_query"
    bot: str = Field(min_length=1, max_length=32)
    query: str = Field(min_length=1, max_length=64)


WarmingAction = Annotated[
    WarmGetDialogs
    | WarmReadContacts
    | WarmReadNotifySettings
    | WarmCheckSettings
    | WarmViewProfile
    | WarmSearchMessages
    | WarmLinkPreview
    | WarmBrowseStickers
    | WarmSavedGifs
    | WarmInlineQuery,
    Field(discriminator="action_type"),
]
