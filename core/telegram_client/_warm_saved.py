"""Warming writes that stay inside Saved Messages: a note, a reminder, a draft, a forward.

Every destination is ``InputPeerSelf()`` and it is hardcoded here — no model carries a
peer, so nothing can be aimed at another account. Log rows carry lengths, ids and
flags only; the note text never leaves the request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.messages import (
    DeleteScheduledMessagesRequest,
    ForwardMessagesRequest,
    SaveDraftRequest,
    SendMessageRequest,
)
from telethon.tl.types import (
    InputPeerSelf,
    UpdateMessageID,
    UpdateNewMessage,
    UpdateNewScheduledMessage,
    UpdateShortSentMessage,
)

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._media import ProfileGatewayError

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import WarmForwardToSaved, WarmSaveDraft, WarmSelfNote

# A refused ``schedule_date`` is a caller bug, not a Telegram mood: a stable-code failure.
_BAD_SCHEDULE_ERRORS = (errors.ScheduleDateTooLateError, errors.ScheduleDateInvalidError)
# The post is gone or cannot be forwarded — nothing to do this cycle. ``ChannelPrivate``
# is left to the ladder: it says something about the channel, not this post.
_FORWARD_SKIPS: dict[type[Exception], str] = {
    errors.ChatForwardsRestrictedError: "forwards_restricted",
    errors.MessageIdInvalidError: "post_gone",
    errors.MessageIdsEmptyError: "post_gone",
    errors.MediaEmptyError: "media_empty",
}


def _sent_message_id(updates: object) -> int | None:
    """Id of the message a send/forward produced, whichever ``Updates`` shape came back."""
    if isinstance(updates, UpdateShortSentMessage):
        return updates.id
    for update in getattr(updates, "updates", None) or ():
        if isinstance(update, UpdateMessageID):
            return update.id
        if isinstance(update, UpdateNewMessage | UpdateNewScheduledMessage):
            return update.message.id
    return None


async def self_note(client: TelegramClient, action: WarmSelfNote) -> _DispatchResult:
    """A short note to Saved Messages; with ``schedule_in_hours`` it is a reminder.

    ``cancel_reminder`` deletes the reminder again in the same dispatch — humans do
    cancel reminders, and one that fires would otherwise need cleaning up later.
    """
    schedule_date = None
    if action.schedule_in_hours is not None:
        # Whole minutes: the client's picker only offers those, so seconds would fingerprint.
        schedule_date = (datetime.now(UTC) + timedelta(hours=action.schedule_in_hours)).replace(
            second=0, microsecond=0
        )
    try:
        updates = await client(
            SendMessageRequest(
                peer=InputPeerSelf(),
                message=action.text,
                no_webpage=True,
                schedule_date=schedule_date,
            ),
        )
    except _BAD_SCHEDULE_ERRORS as exc:
        raise ProfileGatewayError("bad_schedule") from exc  # noqa: EM101
    except errors.ScheduleTooMuchError:
        return _DispatchResult(log_extra={"warm_skip": "schedule_full"})
    message_id = _sent_message_id(updates)
    cancelled = action.cancel_reminder and schedule_date is not None and message_id is not None
    if cancelled:
        await client(DeleteScheduledMessagesRequest(peer=InputPeerSelf(), id=[message_id]))
    return _DispatchResult(
        message_id=message_id,
        log_extra={"text_len": len(action.text), "cancelled": cancelled},
    )


async def save_draft(client: TelegramClient, action: WarmSaveDraft) -> _DispatchResult:
    """Save — or, with empty text, clear — the Saved Messages draft; nothing is sent."""
    await client(SaveDraftRequest(peer=InputPeerSelf(), message=action.text, no_webpage=True))
    return _DispatchResult(log_extra={"text_len": len(action.text)})


async def forward_to_saved(client: TelegramClient, action: WarmForwardToSaved) -> _DispatchResult:
    """Forward one post just read into Saved Messages, as the share sheet's "Saved" does."""
    source = await client.get_input_entity(action.channel)
    try:
        updates = await client(
            ForwardMessagesRequest(
                from_peer=source,
                id=[action.message_id],
                to_peer=InputPeerSelf(),
            ),
        )
    except tuple(_FORWARD_SKIPS) as exc:
        return _DispatchResult(log_extra={"warm_skip": _FORWARD_SKIPS[type(exc)]})
    return _DispatchResult(
        message_id=_sent_message_id(updates),
        log_extra={"channel": action.channel, "source_id": action.message_id},
    )
