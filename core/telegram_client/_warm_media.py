"""Warming writes on a post's media — a vote in a poll the account just read.

Only anonymous, open, non-quiz polls qualify: a public poll would list this account
among voters next to its pool-mates, and a quiz answer cannot be retracted. Log rows
carry the post id and the answer count only — never the question or the answer text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.messages import SendVoteRequest
from telethon.tl.types import MessageMediaPoll

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._warm_browse import _fetch_channel_messages

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.tl.types import Poll

    from schemas.telegram_actions import WarmVoteInPoll

# The poll moved on between our read and the vote — nothing to do this cycle.
# ``ChannelPrivate`` and the flood family stay with the ladder.
_VOTE_SKIPS: dict[type[Exception], str] = {
    errors.MessagePollClosedError: "poll_closed",
    errors.RevoteNotAllowedError: "already_voted",
    errors.MessageIdInvalidError: "post_gone",
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
