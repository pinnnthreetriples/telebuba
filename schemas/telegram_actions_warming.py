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


class WarmSelfNote(BaseModel):
    """Write: a short note to Saved Messages, optionally as a reminder (``schedule_date``).

    ``schedule_in_hours`` set → a scheduled message ("reminder"); ``cancel_reminder``
    then deletes it again in the same dispatch (humans do cancel reminders), so the
    reminder never fires.
    """

    action_type: Literal["warm_self_note"] = "warm_self_note"
    text: str = Field(min_length=2, max_length=60)
    schedule_in_hours: float | None = Field(default=None, gt=0, le=24 * 30)
    cancel_reminder: bool = False


class WarmSaveDraft(BaseModel):
    """Write (server-side only): save a draft to Saved Messages; empty ``text`` clears it."""

    action_type: Literal["warm_save_draft"] = "warm_save_draft"
    text: str = Field(max_length=256)


class WarmForwardToSaved(BaseModel):
    """Write: forward one post the account just read into Saved Messages."""

    action_type: Literal["warm_forward_to_saved"] = "warm_forward_to_saved"
    channel: str
    message_id: int = Field(ge=1)


class WarmVoteInPoll(BaseModel):
    """Write: vote once in an open, anonymous, non-quiz poll among posts just read.

    Core fetches ``message_ids``, keeps polls with ``closed``/``public_voters``/``quiz``
    unset and no ``chosen`` answer, and votes for exactly one option (``option_index``
    modulo the answer count) — a public poll would list this account among voters
    next to its pool-mates, a quiz cannot be retracted. No poll → skip ``no_poll``.
    """

    action_type: Literal["warm_vote_in_poll"] = "warm_vote_in_poll"
    channel: str
    message_ids: list[int] = Field(min_length=1, max_length=5)
    option_index: int = Field(default=0, ge=0)


class WarmToggleArchive(BaseModel):
    """Write: move a joined channel into the archive folder (or back)."""

    action_type: Literal["warm_toggle_archive"] = "warm_toggle_archive"
    channel: str
    archived: bool


class WarmMutePeer(BaseModel):
    """Write: mute a joined channel for ``mute_hours`` (``0`` unmutes)."""

    action_type: Literal["warm_mute_peer"] = "warm_mute_peer"
    channel: str
    mute_hours: float = Field(ge=0, le=24 * 365)


class WarmConsumeMedia(BaseModel):
    """Write-ish: "watch" a video or "listen" to a voice note among posts just read.

    Core registers the view (``messages.getMessagesViews`` with ``increment``) and
    downloads at most ``max_bytes`` of the file — a bounded partial download is what
    a phone does when a video starts playing. The service debits the per-cycle byte
    budget BEFORE dispatch, so a cancelled download stays spent.
    """

    action_type: Literal["warm_consume_media"] = "warm_consume_media"
    channel: str
    message_ids: list[int] = Field(min_length=1, max_length=5)
    kind: Literal["video", "voice"]
    max_bytes: int = Field(ge=65_536, le=50_000_000)


class WarmEmojiStatus(BaseModel):
    """Write (Premium only): set a default emoji status that expires by itself, or clear it."""

    action_type: Literal["warm_emoji_status"] = "warm_emoji_status"
    # Which of the default statuses to use (modulo the list length); randomness is the
    # service's. ``until_hours`` makes it self-expiring, so no second write is needed.
    status_index: int = Field(default=0, ge=0)
    until_hours: float = Field(default=6.0, gt=0, le=24 * 7)
    clear: bool = False


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
    | WarmInlineQuery
    | WarmSelfNote
    | WarmSaveDraft
    | WarmForwardToSaved
    | WarmVoteInPoll
    | WarmToggleArchive
    | WarmMutePeer
    | WarmConsumeMedia
    | WarmEmojiStatus,
    Field(discriminator="action_type"),
]
