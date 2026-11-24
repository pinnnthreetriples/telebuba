"""End a session this process made and could not finish with: a leftover authorization.

A crash or a failed logout in a flow that makes a new key (re-key, second device) can leave
its ``.session`` file behind with a live authorization in it. The repair
(``services.accounts._key_repair``) ends it from the file itself: this opens the file under
the account's proxy and device, asks Telegram to log it out and, only on proof that the
authorization is gone, removes the file. Anything unproven keeps the file for the next try.

The caller holds ``account_auth_lock`` (a live flow is never touched); the lock is not
taken here, it is not re-entrant. The file is a scratch or second-device name no pooled
client opens, so no tombstone is needed either.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from telethon.tl.functions.auth import LogOutRequest

from core.telegram_client._auth import _remove_session_file
from core.telegram_client._auth_bounds import auth_deadline, safe_disconnect
from core.telegram_client._client import create_telegram_client, prepare_telegram_client_profile
from core.telegram_client._session_audit import _PROVEN_DEAD_KEY
from schemas.device_fingerprint import TelegramClientRequest

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceFingerprint, DeviceType

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)

__all__ = ["end_leftover_session"]


async def end_leftover_session(
    account_id: str,
    device_type: DeviceType,
    session_name: str,
    *,
    device: DeviceFingerprint | None = None,
) -> bool:
    """Log the authorization in ``<session dir>/<session_name>.session`` out; ``True`` on proof.

    Proof is ``auth.logOut`` answering, or Telegram refusing because the key is already
    dead (:data:`_PROVEN_DEAD_KEY`, nothing weaker). A network fault, a timeout or a missing
    proxy proves nothing: ``False``, the file untouched. On ``True`` the file is removed
    (into the quarantine). Never raises.
    """
    try:
        profile = await prepare_telegram_client_profile(
            TelegramClientRequest(
                account_id=account_id,
                device_type=device_type,
                session_name=session_name,
                receive_updates=False,
                new_key=True,
            ),
            device=device,
        )
        client = create_telegram_client(profile)
    except Exception as exc:  # noqa: BLE001 - no client, no proof: the file stays.
        logger.warning("leftover session not opened (%s)", type(exc).__name__)
        return False
    ended = False
    try:
        async with asyncio.timeout(auth_deadline()):
            await client.connect()
            with contextlib.suppress(*_PROVEN_DEAD_KEY):
                await client(LogOutRequest())
            ended = True
    except Exception as exc:  # noqa: BLE001 - not proven ended; the class is the only note.
        logger.warning("leftover session logout failed (%s)", type(exc).__name__)
    finally:
        await safe_disconnect(client)
    if ended:
        try:
            await _remove_session_file(profile.session_path)
        except OSError as exc:
            logger.warning("leftover session file kept (%s)", type(exc).__name__)
    return ended
