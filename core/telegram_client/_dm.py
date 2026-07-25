"""Direct-message dispatch: resolving the partner, and sending as a human would.

Split from :mod:`core.telegram_client._actions` for the file-size budget, the
same way channel / media / profile dispatch already live beside it. The executor
keeps the error classification; everything DM-shaped lives here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors, utils
from telethon.tl.functions.contacts import ResolvePhoneRequest

from core.config import settings

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.tl.types import TypeInputPeer

    from schemas.telegram_actions import MarkDirectMessageRead, SendDirectMessage


class DmPeerUnresolvedError(Exception):
    """A DM partner this session cannot address, and no way left to learn how.

    Either we hold no usable phone, or their "who can find me by phone number"
    privacy setting hides them. Its own type so callers can skip the pair
    instead of counting a permanent block as a cycle failure — which means it
    must never stand in for a transient failure the next cycle would survive.
    """


def _typing_seconds(text: str, wpm: int | None = None) -> float:
    """Length-proportional typing time (≈ WPM), clamped to a sane window.

    ``wpm`` is the per-account tempo; ``None`` falls back to the global default.
    """
    warm = settings.warming
    base = len(text) * 60.0 / (5.0 * (wpm or warm.typing_wpm))
    return max(warm.typing_sim_min_seconds, min(warm.typing_sim_max_seconds, base))


async def _resolve_dm_peer(
    client: TelegramClient,
    action: SendDirectMessage | MarkDirectMessageRead,
) -> TypeInputPeer:
    """Resolve the DM partner to an input peer, looking them up by phone if unknown.

    A cold session raises ``ValueError`` on a raw ``user_id`` — it checks the
    in-memory and session caches before the network, so a miss here is a real
    miss. ``resolvePhone`` then answers *without* saving a contact; an import
    would build a saved list of nothing but fleet accounts, exactly the
    correlated graph the rest of warming works to avoid. Telethon files the
    ``access_hash`` from the response into the session, so it is paid once per
    pair.

    Only permanent conditions raise ``DmPeerUnresolvedError``. Telethon turns
    exhausted retries on a server error into a bare ``ValueError``, so the
    lookup RPC is guarded narrowly: a wobbling datacentre must surface as a
    failure the pair retries, never as "this partner is unreachable forever".
    """
    try:
        return await client.get_input_entity(action.user_id)
    except ValueError as cold:
        if action.peer_phone is None:
            msg = f"No cached entity and no phone to resolve {action.user_id}"
            raise DmPeerUnresolvedError(msg) from cold
        # Raw requests skip the normalisation Telethon's high-level helpers
        # apply. parse_phone returns None only for a value that cannot be a
        # number at all, so that is a data bug — never spend an RPC on it.
        phone = utils.parse_phone(action.peer_phone)
        if phone is None:
            msg = f"Stored phone for {action.user_id} is not a usable number"
            raise DmPeerUnresolvedError(msg) from cold
        try:
            await client(ResolvePhoneRequest(phone))
        except errors.PhoneNotOccupiedError as hidden:
            msg = f"No reachable account uses the phone stored for {action.user_id}"
            raise DmPeerUnresolvedError(msg) from hidden
        try:
            return await client.get_input_entity(action.user_id)
        except ValueError as missing:
            msg = f"Phone lookup did not reveal {action.user_id}"
            raise DmPeerUnresolvedError(msg) from missing


async def _send_dm_with_typing(client: TelegramClient, action: SendDirectMessage) -> int | None:
    """Send a DM, optionally preceded by a length-proportional "typing…" action."""
    peer = await _resolve_dm_peer(client, action)
    if settings.warming.typing_simulation_enabled:
        async with client.action(peer, "typing"):  # ty: ignore[invalid-context-manager]
            await asyncio.sleep(_typing_seconds(action.text, action.typing_wpm))
            message = await client.send_message(peer, action.text)
    else:
        message = await client.send_message(peer, action.text)
    return int(getattr(message, "id", 0)) or None
