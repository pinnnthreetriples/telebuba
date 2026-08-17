"""Chat-scoped Telegram actions — resolve a chat, react in it, copy media into it.

A sibling of :mod:`schemas.telegram_actions` for the file-size cap; every name is
re-imported there, so ``from schemas.telegram_actions import ResolveChat`` keeps
working and the discriminated unions stay in one place.

**The chat-id sign convention, pinned here.** Every ``chat_id`` in this module —
and every id these actions hand back — is Telegram's RAW POSITIVE id, with no
``-100`` marker. Two things fix that choice: ``PostComment.chat_id`` already
carries one (``services.neurocomment.challenge`` feeds it
``LinkedDiscussionGroupResult.linked_chat_id``, read straight off
``full_chat.linked_chat_id``), and ``core.telegram_client._channels._input_channel``
refuses anything ``<= 0``. Telethon reads an unmarked positive int by searching
the session entity cache across users, chats AND channels without an RPC, which is
also why the marked form is confined to the push listener and never reaches here.

**A resolved id belongs to ONE account.** That cache is a per-account SQLite
session file, so an id account A resolved is meaningless to account B: B raises
``ValueError: Could not find the input entity``, which the executor folds into a
generic failure. Callers therefore resolve per account and treat that ``ValueError``
as "not a member", never as a transport fault.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field

# The eight reactions the neuroshilling scenario form offers. Fixed rather than
# free-form because a non-Premium account may place exactly ONE reaction per
# message and custom emoji need Premium, so a wider set would only produce
# ``ReactionInvalidError`` at run time. The chat's own allowed-emoji whitelist
# narrows this further at dispatch.
ChatReactionEmoji = Literal["👍", "❤️", "🔥", "👏", "🤔", "💯", "✨", "🙌"]

# What a target turned out to be. ``basic_group`` and ``user`` share ONE message-id
# sequence PER USER (core.telegram.org/api/channel), so account A's ``msg_id`` is not
# account B's and a cross-account reply chain would misfire silently — the reason the
# domain refuses both rather than merely noting them.
ChatKind = Literal["channel", "megagroup", "basic_group", "user"]

# What a message carries, as the copy path classifies it. ``web_page`` is called out
# because ``send_file`` raises ``TypeError`` on a ``MessageMediaWebPage``, and
# ``unsupported`` covers ``MessageMediaEmpty`` / ``MessageMediaUnsupported``, which send
# NOTHING at all rather than failing — both are refusals, not "no media".
ChatMediaKind = Literal["none", "photo", "document", "web_page", "unsupported"]
# The only two of those ``send_file`` re-sends faithfully. It lives next to the kinds
# themselves because two layers ask the same question of the same vocabulary — the
# gateway refuses at dispatch, and the approval gate refuses before a campaign can be
# started at all — and two copies of it would drift apart silently, each still passing
# its own tests.
COPYABLE_MEDIA_KINDS: Final = frozenset({"photo", "document"})


class ResolveChat(BaseModel):
    """Read-only: turn a target token into THIS account's own chat id and kind.

    ``target`` is a normalised :func:`core.channel_tokens.normalize_channel` token —
    a bare username or a ``+HASH`` invite key. An invite key can only be resolved by
    an account that has already joined, which is exactly the per-pair state the
    caller is tracking.
    """

    action_type: Literal["resolve_chat"] = "resolve_chat"
    target: str = Field(min_length=1)


class ReadChatMessages(BaseModel):
    """Read-only: re-read messages by id in an arbitrary chat.

    ``chat`` is a peer reference rather than an int so a message LINK can be checked
    before anything is resolved: an all-digit value is fed to Telethon as the raw
    positive id above, anything else as a username.
    """

    action_type: Literal["read_chat_messages"] = "read_chat_messages"
    chat: str = Field(min_length=1)
    message_ids: list[int] = Field(min_length=1, max_length=100)


class ReactToMessage(BaseModel):
    """React to ONE named message in ONE named chat with ONE operator-chosen emoji.

    Distinct from ``ReactToPost``, which picks a random recent post AND a random
    emoji out of a configured pool. Here both are given, so the channel's
    allowed-emoji whitelist can only ever SKIP the step, never substitute for it.
    """

    action_type: Literal["react_to_message"] = "react_to_message"
    chat_id: int = Field(gt=0)
    message_id: int = Field(gt=0)
    emoji: ChatReactionEmoji


class CopyMessageMedia(BaseModel):
    """Re-send another message's media as our own — a COPY, never a forward.

    A forward renders "Forwarded from …" and links back to the source, which
    defeats the point. ``send_file(chat, message.media, ...)`` reuses the existing
    file reference, so nothing is re-uploaded.

    ``source_chat`` follows ``ReadChatMessages.chat``. The reference is re-read
    immediately before every send and NEVER cached in the database: Telegram's file
    references expire, and a stale one is a refusal the operator cannot act on.
    """

    action_type: Literal["copy_message_media"] = "copy_message_media"
    chat_id: int = Field(gt=0)
    source_chat: str = Field(min_length=1)
    source_message_id: int = Field(gt=0)
    caption: str = ""
    reply_to: int | None = None
