"""Warming writes on a post's media — a poll vote, a video watched, a voice note heard.

Only anonymous, open, non-quiz polls qualify: a public poll would list this account
among voters next to its pool-mates, and a quiz answer cannot be retracted. "Watching"
is a view counted plus a bounded partial download — what a phone does when a video
starts playing. Log rows carry the post id, the answer count, the kind and the byte
count only — never the question, the answer text, a caption or a filename.
"""

from __future__ import annotations

import asyncio
import math
from contextlib import suppress
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.channels import ReadMessageContentsRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest, SendVoteRequest
from telethon.tl.types import (
    Document,
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPoll,
)

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._media import ProfileGatewayError
from core.telegram_client._warm_browse import _fetch_channel_messages

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telethon import TelegramClient
    from telethon.tl.types import Poll

    from schemas.telegram_actions import WarmConsumeMedia, WarmVoteInPoll

# The poll moved on between our read and the vote — nothing to do this cycle.
# ``ChannelPrivate`` and the flood family stay with the ladder.
_VOTE_SKIPS: dict[type[Exception], str] = {
    errors.MessagePollClosedError: "poll_closed",
    errors.RevoteNotAllowedError: "already_voted",
    errors.MessageIdInvalidError: "post_gone",
}
# 128 KiB: a multiple of Telethon's 4 KiB minimum, under its 512 KiB cap, and the
# part size official clients stream video with.
_CHUNK = 128 * 1024
# The service enforces the byte budget; this only stops a stalled DC from holding the
# cycle. Whatever arrived before it fires still counts as watched.
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_STALE_REFERENCE_ERRORS = (
    errors.FileReferenceExpiredError,
    errors.FileReferenceInvalidError,
    errors.FilerefUpgradeNeededError,
)
_BAD_CHUNK_ERRORS = (errors.LimitInvalidError, errors.OffsetInvalidError)
# The file is gone or unreachable for this account — nothing to watch this cycle.
_MEDIA_SKIPS: dict[type[Exception], str] = {
    errors.LocationInvalidError: "media_unavailable",
    errors.FileIdInvalidError: "media_unavailable",
    errors.MediaEmptyError: "media_unavailable",
}


def _votable_poll(message: object) -> tuple[int, Poll] | None:
    """``(post id, poll)`` for an open anonymous non-quiz poll this account has not voted in."""
    media = getattr(message, "media", None)
    if not isinstance(media, MessageMediaPoll):
        return None
    poll = media.poll
    if not poll.answers or poll.closed or poll.public_voters or poll.quiz:
        return None
    # A ``chosen`` flag on any answer is Telegram's own "already voted" — no table needed.
    if any(v.chosen for v in media.results.results or ()):
        return None
    return getattr(message, "id", 0), poll


async def vote_in_poll(client: TelegramClient, action: WarmVoteInPoll) -> _DispatchResult:
    """Vote for exactly one option in the first eligible poll among the posts just read."""
    peer, messages = await _fetch_channel_messages(client, action.channel, action.message_ids)
    found = next(filter(None, map(_votable_poll, messages)), None)
    if found is None:
        return _DispatchResult(log_extra={"warm_skip": "no_poll"})
    message_id, poll = found
    # One option even for ``multiple_choice``: a human rarely ticks several.
    answer = poll.answers[action.option_index % len(poll.answers)]
    try:
        # ``Poll.answers`` is typed with the input variant too; the server sends ``PollAnswer``.
        await client(SendVoteRequest(peer=peer, msg_id=message_id, options=[answer.option]))  # ty: ignore[invalid-argument-type, unresolved-attribute]
    except tuple(_VOTE_SKIPS) as exc:
        return _DispatchResult(log_extra={"warm_skip": _VOTE_SKIPS[type(exc)]})
    return _DispatchResult(message_id=message_id, log_extra={"options": len(poll.answers)})


def _is_kind(document: Document, kind: str) -> bool:
    """Video: a non-round ``video/*``. Voice: a voice note or a round video message."""
    attrs = document.attributes
    if kind == "video":
        return document.mime_type.startswith("video/") and any(
            isinstance(a, DocumentAttributeVideo) and not a.round_message for a in attrs
        )
    return any(
        (isinstance(a, DocumentAttributeAudio) and a.voice)
        or (isinstance(a, DocumentAttributeVideo) and a.round_message)
        for a in attrs
    )


def _consumable(messages: Sequence[object], kind: str) -> tuple[int, Document] | None:
    """``(post id, document)`` of the first post carrying a sized file of ``kind``."""
    for message in messages:
        media = getattr(message, "media", None)
        document = getattr(media, "document", None)
        if (
            isinstance(media, MessageMediaDocument)
            and isinstance(document, Document)  # never ``DocumentEmpty``
            and document.size
            and _is_kind(document, kind)
        ):
            return getattr(message, "id", 0), document
    return None


async def _stream(client: TelegramClient, document: Document, max_bytes: int) -> int:
    """Pull the first ``max_bytes`` (or the whole file, if smaller) and return the byte count.

    ``iter_download`` is the one sanctioned convenience wrapper: it owns DC migration and
    CDN redirects, which raw ``upload.getFile`` would make this module re-implement.
    """
    budget = min(max_bytes, document.size)
    stream = client.iter_download(document, request_size=_CHUNK, limit=math.ceil(budget / _CHUNK))
    consumed = 0
    deadline = asyncio.timeout(_DOWNLOAD_TIMEOUT_SECONDS)
    try:
        async with deadline:
            async for chunk in stream:
                consumed += len(chunk)
    except TimeoutError:
        # Only OUR deadline is a slow file (what arrived was "watched", and the budget is
        # spent); a ``TimeoutError`` raised inside the stream is a failure like any other.
        if not deadline.expired():
            raise
    finally:
        # Hands a borrowed media-DC sender back; a no-op once the iterator finished. Telethon
        # only creates ``_sender`` on the first pull, so a stream that never got that far
        # (cancel or deadline mid-handshake) has nothing to close — and that must not
        # replace the exception already in flight.
        with suppress(AttributeError):
            await stream.close()
    return consumed


async def _download(
    client: TelegramClient, action: WarmConsumeMedia, message_id: int, document: Document
) -> int | None:
    """Bytes consumed, or ``None`` when the file reference went stale twice."""
    with suppress(*_STALE_REFERENCE_ERRORS):
        return await _stream(client, document, action.max_bytes)
    # The reference we read expired before the download started; a fresh read of that
    # one post carries a new one. Once only — a second refusal is not staleness.
    _, messages = await _fetch_channel_messages(client, action.channel, [message_id])
    found = _consumable(messages, action.kind)
    if found is None:
        return None
    try:
        return await _stream(client, found[1], action.max_bytes)
    except _STALE_REFERENCE_ERRORS:
        return None


async def consume_media(client: TelegramClient, action: WarmConsumeMedia) -> _DispatchResult:
    """Count a view on the first post with a ``kind`` file, then stream its opening bytes.

    A voice note is also marked listened (``readMessageContents``), as a player does;
    a video is not — clients only send that for self-destructing media.
    """
    peer, messages = await _fetch_channel_messages(client, action.channel, action.message_ids)
    found = _consumable(messages, action.kind)
    if found is None:
        return _DispatchResult(log_extra={"warm_skip": "no_media"})
    message_id, document = found
    try:
        await client(GetMessagesViewsRequest(peer=peer, id=[message_id], increment=True))  # ty: ignore[invalid-argument-type]
    except errors.MsgIdInvalidError:
        return _DispatchResult(log_extra={"warm_skip": "post_gone"})
    try:
        consumed = await _download(client, action, message_id, document)
    except tuple(_MEDIA_SKIPS) as exc:
        return _DispatchResult(log_extra={"warm_skip": _MEDIA_SKIPS[type(exc)]})
    except _BAD_CHUNK_ERRORS as exc:
        raise ProfileGatewayError("bad_chunk") from exc  # noqa: EM101
    if consumed is None:
        return _DispatchResult(log_extra={"warm_skip": "stale_reference"})
    if not consumed:
        # The deadline fired before the first chunk: nothing played, nothing to mark heard.
        return _DispatchResult(log_extra={"warm_skip": "download_stalled"})
    if action.kind == "voice":
        await client(ReadMessageContentsRequest(channel=peer, id=[message_id]))  # ty: ignore[invalid-argument-type]
    return _DispatchResult(
        message_id=message_id, log_extra={"kind": action.kind, "bytes": consumed}
    )
