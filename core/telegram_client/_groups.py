"""Membership dispatch: joining channels and their linked discussion groups.

Split out of :mod:`core.telegram_client._actions` to keep that module under the
file-size cap — the same reason ``_channels``, ``_dm`` and ``_media`` exist.
``_actions`` keeps the ``match`` arms and delegates the bodies here.

The discussion-group pair is the interesting half: comment membership lives in
the group linked to a channel, not on the broadcast channel itself, and that
group usually has no username — so both join and leave resolve the entity first
via :func:`_resolve_linked_group`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    JoinChannelRequest,
    LeaveChannelRequest,
)
from telethon.tl.functions.messages import ImportChatInviteRequest

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._util import extract_invite_hash

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import (
        JoinChannel,
        JoinDiscussionGroup,
        LeaveDiscussionGroup,
    )


async def dispatch_join_channel(client: TelegramClient, action: JoinChannel) -> None:
    """Join a public channel by handle, or a private one by invite link/hash."""
    hash_str = extract_invite_hash(action.channel)
    if hash_str:
        await client(ImportChatInviteRequest(hash=hash_str))
    else:
        await client(JoinChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]


async def _resolve_linked_group(client: TelegramClient, channel: str) -> object:
    """Resolve ``channel``'s linked discussion group — the peer join and leave act on.

    ``GetFullChannelRequest`` returns a ``messages.ChatFull`` whose ``full_chat``
    carries ``linked_chat_id`` and whose ``chats`` list holds the resolved
    ``Channel`` entities (with ``access_hash``). We act on that entity directly —
    the linked group has no username, so it can't be addressed by handle. A
    ``None`` ``linked_chat_id`` (comments disabled) raises ``ValueError`` so the
    executor classifies it as a generic failure rather than silently no-op.
    """
    full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    linked = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
    if linked is None:
        msg = f"No linked discussion group for {channel!r}"
        raise ValueError(msg)
    linked_id = int(linked)
    entity = next(
        (chat for chat in getattr(full, "chats", []) if int(getattr(chat, "id", 0)) == linked_id),
        None,
    )
    if entity is None:
        msg = f"Linked group {linked_id} not in ChatFull.chats for {channel!r}"
        raise ValueError(msg)
    return entity


async def dispatch_join_discussion_group(
    client: TelegramClient,
    action: JoinDiscussionGroup,
) -> None:
    """Join the discussion group linked to ``channel`` — the commenting membership."""
    entity = await _resolve_linked_group(client, action.channel)
    await client(JoinChannelRequest(channel=entity))  # ty: ignore[invalid-argument-type]


async def dispatch_leave_discussion_group(
    client: TelegramClient,
    action: LeaveDiscussionGroup,
) -> _DispatchResult:
    """Leave ``channel``'s linked discussion group — the mirror of the join.

    Already being out of the group is the state the caller asked for, not a
    failure: Telegram reports it as ``UserNotParticipantError``, which would
    otherwise reach ``_generic_error`` and cost the operator an ERROR row plus a
    stderr traceback for a no-op — the same noise ``already_participant`` and
    ``join_by_request`` were special-cased to stop. It rides back in the log
    extra instead, so a no-op is still distinguishable from a real leave.
    """
    entity = await _resolve_linked_group(client, action.channel)
    try:
        await client(LeaveChannelRequest(channel=entity))  # ty: ignore[invalid-argument-type]
    except errors.UserNotParticipantError:
        return _DispatchResult(log_extra={"already_left": True})
    return _DispatchResult()
