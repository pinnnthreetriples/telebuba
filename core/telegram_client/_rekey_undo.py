"""Undoing a failed re-key attempt: end the new authorization, drop its scratch file.

Split out of ``_rekey`` (file size). The undo never trades one leak for another: a file is
removed only once the authorization it holds is provably ended, and the intent stays open
(for ``services.accounts._key_repair``) whenever something is left that a person or a later
pass must still look at. Every step is bounded: a hung call must not hold the account.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.telegram_client import _rekey_files as _files
from core.telegram_client._auth import _remove_session_file
from core.telegram_client._key_intent import close_quietly

if TYPE_CHECKING:
    from telethon import TelegramClient

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)

_RECONNECT_TIMEOUT_S = 20.0


def _client_key(client: object) -> bytes | None:
    """The auth key a client holds in memory (kept after ``disconnect``); ``None`` if unknown."""
    key = getattr(getattr(getattr(client, "session", None), "auth_key", None), "key", None)
    return bytes(key) if isinstance(key, (bytes, bytearray)) else None


async def _may_hold_new_key(new: TelegramClient, final_file: str) -> bool:
    """Whether the account's own file may hold ``new``'s key: anything but proof it does not."""
    held = await asyncio.to_thread(_files.file_key, final_file)
    mine = _client_key(new)
    return held is None or mine is None or held == mine


async def _disconnect_quietly(client: TelegramClient) -> None:
    try:
        async with asyncio.timeout(settings.telegram.signin_probe_timeout_seconds):
            await client.disconnect()
    except Exception as exc:  # noqa: BLE001 - best effort; the authorization is kept on purpose.
        logger.warning("re-key disconnect failed (%s)", type(exc).__name__)


async def _log_out(new: TelegramClient) -> bool:
    """End the new authorization; ``True`` only when Telegram confirmed it."""
    if not new.is_connected():
        # The swap stage disconnects ``new`` first; ``log_out`` on a closed client would
        # fail and leave the new authorization standing, orphaned with no session file.
        try:
            await asyncio.wait_for(new.connect(), _RECONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - the logout below then fails too; logged.
            logger.warning("re-key reconnect failed (%s)", type(exc).__name__)
    try:
        async with asyncio.timeout(settings.telegram.signin_probe_timeout_seconds):
            # Telethon answers ``False`` for a refusal: not a confirmation either.
            return (await new.log_out()) is not False
    except Exception as exc:  # noqa: BLE001 - not confirmed; the file stays below.
        logger.warning("re-key logout failed (%s)", type(exc).__name__)
        return False


async def _discard(
    new: TelegramClient,
    scratch_path: str,
    *,
    signed_in: bool,
    final_file: str | None = None,
    intent_id: int | None = None,
) -> None:
    """Undo a failed attempt: end the new authorization, disconnect, remove its file.

    Each step runs whatever the one before did: a disconnect that raises must not leave the
    half-made ``.session`` behind to refuse every retry.

    ``final_file`` is the account's own file once the swap may have written it. The new
    authorization is then ended only on proof that this file does not hold its key: ending
    it otherwise could end the account's only working key. Kept, it stays signed in with its
    scratch file in place, the warning says so, and the intent stays open.

    A logout Telegram did not confirm keeps the scratch file and the intent open: the file
    is the only handle on that authorization, and the repair ends it from there. A confirmed
    undo (or an attempt that never signed in) removes the file and closes the intent
    ``undone``.
    """
    if signed_in and final_file is not None and await _may_hold_new_key(new, final_file):
        logger.warning("re-key undo kept the new authorization: the account's file may hold it")
        await _disconnect_quietly(new)
        return
    confirmed = await _log_out(new) if signed_in else True
    await _disconnect_quietly(new)
    if not confirmed:
        logger.warning("re-key undo kept the scratch session: the logout was not confirmed")
        return
    try:
        await _remove_session_file(scratch_path)
    except OSError as exc:
        logger.warning("re-key scratch session kept (%s)", type(exc).__name__)
        return
    await close_quietly(intent_id, "undone")
