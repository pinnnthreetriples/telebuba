"""Masstagging dispatch: the auto-delete toggle, resolving a target, an account's contacts.

The ``masstag_*`` write enters through ``_action_families`` (one table row) and the two
reads through ``_read_channels``' fallback match. Classification stays in
``execute``/``execute_read``: the toggle raises Telethon's own errors and lets the ladder
map them. The resolve read answers "nobody by that name" itself, because Telethon says
it with a bare ``ValueError`` the read ladder does not catch.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.messages import SetHistoryTTLRequest
from telethon.tl.types import Channel, Chat, User

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._read_chat import peer_reference
from schemas.telegram_actions_masstagging import (
    ListMasstagContacts,
    MasstagContactsResult,
    MasstagResolvedTarget,
    MasstagToggleTtl,
    MasstagUser,
    ResolveMasstagTarget,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction

# "Nobody by that name" in its every Telethon spelling.
_NOT_FOUND_ERRORS = (
    errors.UsernameNotOccupiedError,
    errors.UsernameInvalidError,
    errors.PeerIdInvalidError,
)


async def dispatch_masstag_action(
    client: TelegramClient, action: TelegramAction
) -> _DispatchResult:
    """Run one ``masstag_*`` write; the match narrows ``action`` for ty."""
    match action:
        case MasstagToggleTtl():
            await _toggle_ttl(client, action)
            return _DispatchResult()
        case _:  # pragma: no cover - the family table routes only masstag_* here
            msg = f"Unsupported masstag action_type: {action.action_type}"
            raise ValueError(msg)


def masstag_log_extra(action: TelegramAction) -> dict[str, object]:
    match action:
        case MasstagToggleTtl():
            return {"peer": action.peer}
        case _:  # pragma: no cover - the family table routes only masstag_* here
            return {}


async def _toggle_ttl(client: TelegramClient, action: MasstagToggleTtl) -> None:
    peer = await client.get_input_entity(peer_reference(action.peer))
    await client(SetHistoryTTLRequest(peer=peer, period=action.period_seconds))
    await asyncio.sleep(action.hold_seconds)
    await client(SetHistoryTTLRequest(peer=peer, period=0))


def _user(entity: object) -> MasstagUser:
    username = getattr(entity, "username", None)
    first_name = getattr(entity, "first_name", None)
    return MasstagUser(
        user_id=int(getattr(entity, "id", 0) or 0),
        username=username if isinstance(username, str) and username else None,
        first_name=first_name if isinstance(first_name, str) else "",
        is_bot=bool(getattr(entity, "bot", False)),
        is_deleted=bool(getattr(entity, "deleted", False)),
    )


def _is_group(entity: object) -> bool:
    if isinstance(entity, Channel):
        return bool(getattr(entity, "megagroup", False))
    return isinstance(entity, Chat)


async def _members(client: TelegramClient, entity: object, limit: int) -> MasstagResolvedTarget:
    try:
        participants = await client.get_participants(entity, limit=limit)
    except errors.ChatAdminRequiredError:
        return MasstagResolvedTarget(outcome="hidden")
    members = [_user(user) for user in participants if isinstance(user, User)]
    if not members:
        return MasstagResolvedTarget(outcome="hidden")
    return MasstagResolvedTarget(outcome="group", members=members)


async def dispatch_masstag_read(
    client: TelegramClient, action: ResolveMasstagTarget | ListMasstagContacts
) -> MasstagResolvedTarget | MasstagContactsResult:
    match action:
        case ResolveMasstagTarget():
            return await _resolve(client, action)
        case ListMasstagContacts():
            return await _contacts(client, action)


async def _resolve(client: TelegramClient, action: ResolveMasstagTarget) -> MasstagResolvedTarget:
    """A username or a phone, as this account sees it.

    ``get_input_entity`` answers a phone only out of the session cache (the account's
    contacts), so a stranger's phone is "not found" — never imported as a contact.
    """
    target = action.target
    try:
        entity = await client.get_entity(target)
    except _NOT_FOUND_ERRORS:
        return MasstagResolvedTarget(outcome="not_found")
    except ValueError:
        return MasstagResolvedTarget(outcome="not_found")
    if isinstance(entity, User):
        return MasstagResolvedTarget(outcome="user", user=_user(entity))
    if _is_group(entity) and action.members_limit > 0:
        return await _members(client, entity, action.members_limit)
    return MasstagResolvedTarget(outcome="not_user")


async def _contacts(client: TelegramClient, action: ListMasstagContacts) -> MasstagContactsResult:
    result = await client(GetContactsRequest(hash=0))
    users = [_user(user) for user in getattr(result, "users", None) or () if isinstance(user, User)]
    return MasstagContactsResult(users=users[: action.limit])
