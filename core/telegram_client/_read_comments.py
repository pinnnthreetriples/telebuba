"""Comment-thread reads, plus the linked-group resolver every comment probe shares.

Extracted-sibling pattern (see ``_read_channels.py``): ``_read`` keeps the match and
imports the dispatchers. The resolver lives here rather than in ``_read`` because it
exists for exactly one reason — comments live in the channel's linked discussion group,
never on the broadcast channel — and ``_read`` re-imports it, so the patch target
``_read._resolve_linked_group_entity`` the neurocomment sweep documents stays valid.

``_media_kind`` is imported from ``_listener`` rather than re-derived: the reply path must
agree with the live push path on what counts as a photo, or the vision fetch would fire
here for posts the listener classifies as something no comment can be made out of.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.channels import GetFullChannelRequest

from core.telegram_client._listener import _media_kind
from schemas.telegram_actions_comments import (
    PostCommentRecord,
    ReadPostComments,
    ReadPostCommentsResult,
)

if TYPE_CHECKING:
    from telethon import TelegramClient


async def _resolve_linked_group_entity(client: TelegramClient, channel: str) -> object | None:
    """Resolve ``channel``'s linked discussion-group entity, or ``None`` if there is none.

    The ban and deletion probes both act on the linked group — comments live there,
    not on the broadcast channel. ``GetFullChannelRequest`` carries the bare
    ``linked_chat_id`` and *usually* the resolved ``Channel`` in ``chats`` (with
    access_hash), but Telegram omits that entity for some channels, so we fall back to
    ``get_input_entity`` off the warm session cache (the account joined the group at
    onboarding — same idiom as ``_read_challenge``). ``None`` means no linked group /
    comments disabled / the id couldn't be resolved.
    """
    full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    linked = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
    if linked is None:
        return None
    linked_id = int(linked)
    entity = next(
        (chat for chat in getattr(full, "chats", []) if int(getattr(chat, "id", 0)) == linked_id),
        None,
    )
    if entity is not None:
        return entity
    try:
        return await client.get_input_entity(linked_id)
    except (ValueError, TypeError, errors.RPCError):
        return None


def _comment_record(message: object) -> PostCommentRecord:
    """One thread message as a record; ``sender_id`` is ``None`` for a channel-signed post."""
    sender = getattr(message, "sender_id", None)
    return PostCommentRecord(
        message_id=int(getattr(message, "id", 0)),
        sender_id=sender if isinstance(sender, int) else None,
        text=getattr(message, "message", None) or "",
    )


async def dispatch_read_post_comments(
    client: TelegramClient,
    action: ReadPostComments,
) -> ReadPostCommentsResult:
    """Read the post, then the oldest ``limit`` messages of its comment thread.

    In that order, because each read answers a question the next one cannot. The post
    first: ``get_messages`` yields ``None`` for one deleted while the attempt was parked,
    which is ``post_missing`` — a normal answer, and the only one that also explains an
    empty thread without a second guess. Then the linked group, whose absence means
    comments are off; ``messages.getReplies`` errors on such a post, and "nobody has
    commented" must not reach the caller as an exception. Then the thread itself.

    The resolved entity is deliberately discarded: ``iter_messages(channel,
    reply_to=post_id)`` issues ``getReplies`` against the CHANNEL and the post id, and the
    ids it yields are already group ids — which is what a reply has to address. The resolve
    is bought as the comments-enabled precondition, the same read the sibling probes make.

    ``reverse=True`` yields oldest-first, so ``limit`` takes the head of the thread and the
    caller's "the 3rd comment" keeps naming one comment as later ones arrive.
    """
    post = await client.get_messages(action.channel, ids=action.post_id)
    if post is None:
        return ReadPostCommentsResult(comments=[], post_missing=True)
    comments: list[PostCommentRecord] = []
    if await _resolve_linked_group_entity(client, action.channel) is not None:
        comments = [
            _comment_record(message)
            async for message in client.iter_messages(
                action.channel,
                reply_to=action.post_id,
                reverse=True,
                limit=action.limit,
            )
        ]
    return ReadPostCommentsResult(
        comments=comments,
        post_text=getattr(post, "message", None) or "",
        post_media_kind=_media_kind(post),
    )
