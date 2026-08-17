"""Write-rights read: whose mute forbids a write — the chat's, ours, or nobody's.

Extracted from :mod:`core.telegram_client._read` unchanged (that module sits on the
aislop file-size cap and the chat-scoped read arms had to fit), following the same
sibling pattern as ``_read_stories`` / ``_read_channels``. ``_read`` keeps the
``match`` arm and the error ladder; only the body moved.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import ChannelParticipantBanned, InputUserSelf

from core.telegram_client._read_comments import _resolve_linked_group_entity
from schemas.telegram_actions_rights import WriteRightsResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions_rights import CheckWriteRights


def _mute_expiry(rights: object) -> str | None:
    """ISO-8601 expiry of a ``ChatBannedRights``, or ``None`` when it carries none.

    Telegram encodes "forever" as ``until_date=0`` and Telethon's date reader turns a
    zero timestamp into ``None``, so a permanent mute simply has no date; the far-future
    sentinel other clients send arrives as a real datetime instead. Both are handed on
    as-is — deciding how long to wait on either is the caller's rule, not the gateway's.
    """
    until = getattr(rights, "until_date", None)
    return until.isoformat() if isinstance(until, datetime) else None


async def dispatch_check_write_rights(
    client: TelegramClient,
    action: CheckWriteRights,
) -> WriteRightsResult:
    """Read whose mute forbids the write: the whole chat's, this account's, or neither.

    The two RPCs ``_dispatch_check_banned`` already pays for, asked of the two records
    that answer different questions. The chat-wide ``default_banned_rights`` is checked
    FIRST and short-circuits the participant read: a group switched read-only leaves our
    own record untouched, so reading ours first would report a channel-wide switch as a
    personal mute — the confusion this whole action exists to end.

    Slow mode is deliberately not read. It is a third thing, and Telegram answers a
    too-fast send with ``SlowModeWaitError`` (mapped to the ``slow_mode_wait`` cooldown
    status), never ``ChatWriteForbiddenError``, so it cannot reach this read at all.

    Nothing here decides on a failure: no linked group, or a participant record we are
    not in, is ``unknown`` with a content-free reason. Everything else propagates to
    ``execute_read_many``, which collapses it to ``RPC: <ClassName>``.
    """
    entity = await _resolve_linked_group_entity(client, action.channel)
    if entity is None:
        return WriteRightsResult(scope="unknown", reason="no_linked_group")
    if getattr(getattr(entity, "default_banned_rights", None), "send_messages", False):
        return WriteRightsResult(scope="everyone")
    try:
        result = await client(GetParticipantRequest(channel=entity, participant=InputUserSelf()))  # ty: ignore[invalid-argument-type]
    except errors.UserNotParticipantError:
        return WriteRightsResult(scope="unknown", reason="not_member")
    participant = getattr(result, "participant", None)
    if isinstance(participant, ChannelParticipantBanned):
        rights = getattr(participant, "banned_rights", None)
        if getattr(rights, "view_messages", False):
            # Kicked, not muted — ``_classify_participant`` reads the same record the same
            # way. Telegram revokes every right at once on a ban, so without this the record
            # would also satisfy the send_messages test below and a pair that is OUT of the
            # chat would be parked waiting for a mute to lapse. A kick has its own error
            # family (UserNotParticipant / ChannelPrivate) and its own branch.
            return WriteRightsResult(scope="unknown", reason="not_member")
        if getattr(rights, "send_messages", False):
            return WriteRightsResult(scope="self_only", muted_until=_mute_expiry(rights))
    return WriteRightsResult(scope="none")
