"""The re-key's login by token: the new client exports it, the imported session accepts it.

Split out of ``_rekey`` (file size). Every network step is bounded (``auth_deadline``): a hung
call would hold the account's auth lock and the pool tombstone until the next restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import ChangeAuthorizationSettingsRequest
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.config import settings
from core.telegram_client._auth_bounds import auth_deadline
from core.telegram_client._rekey_errors import _FAILED, _TWOFA_MISSING, _refusal
from core.telegram_client._twofa_candidates import sign_in_with_passwords

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from telethon import TelegramClient

# Stdlib sink, like ``_rekey``.
logger = logging.getLogger(__name__)


async def _sign_in_by_token(
    new: TelegramClient,
    approver: TelegramClient,
    account_id: str,
    passwords: Sequence[str],
    accepted: Callable[[str | None], Awaitable[None]],
) -> str:
    """Log ``new`` in with a token ``approver`` accepts; the new authorization's hash.

    ``accepted`` is awaited the moment an authorization exists or may exist (see
    :func:`_accept_token`), BEFORE the wait for the login update: from then on the caller
    must end it when this raises.
    """
    async with asyncio.timeout(auth_deadline()):
        await new.connect()
    async with asyncio.timeout(auth_deadline()):
        qr = await new.qr_login()
    # Telegram answers the acceptance with an update that ``qr.wait`` listens for only once
    # it runs: start waiting BEFORE accepting, or the update lands unheard and the login
    # times out with the token already accepted.
    timeout = settings.telegram.session_check_timeout_seconds
    waiting = asyncio.ensure_future(qr.wait(timeout=timeout))
    await asyncio.sleep(0)
    try:
        new_hash = await _accept_token(approver, qr.token, accepted)
    except BaseException:
        waiting.cancel()
        raise
    try:
        await waiting
    except errors.SessionPasswordNeededError:
        if not passwords:
            raise _refusal(_TWOFA_MISSING) from None
        async with asyncio.timeout(auth_deadline()):
            await sign_in_with_passwords(new, account_id, passwords)
    return str(new_hash)


async def _accept_token(
    approver: TelegramClient, token: bytes, accepted: Callable[[str | None], Awaitable[None]]
) -> int:
    """Have the imported session accept ``token``; the new authorization's hash.

    ``accepted`` is told as soon as an authorization exists OR MAY exist: with its hash when
    Telegram answered, with ``None`` when the request got no answer at all (a timeout, a dropped
    connection, a cancellation). Only a refusal by Telegram (an RPC error) proves nothing was
    created. The confirmation after it is a courtesy and never fails the login.
    """
    try:
        async with asyncio.timeout(auth_deadline()):
            answer = await approver(AcceptLoginTokenRequest(token=token))
    except errors.RPCError:
        raise
    except BaseException:
        # No answer is not a refusal: the undo must try to end what may have been made.
        await asyncio.shield(accepted(None))
        raise
    new_hash = getattr(answer, "hash", None)
    if not isinstance(new_hash, int):
        await accepted(None)
        raise _refusal(_FAILED, "AuthorizationWithoutHash")
    await accepted(str(new_hash))
    if getattr(answer, "unconfirmed", None):
        # A login by a session that is old enough to approve it is the owner's own: say so,
        # as an official client does when the user recognises the new session.
        try:
            async with asyncio.timeout(auth_deadline()):
                await approver(ChangeAuthorizationSettingsRequest(hash=new_hash, confirmed=True))
        except Exception as exc:  # noqa: BLE001 - the login stands; only the class is noted.
            logger.warning("re-key login not confirmed (%s)", type(exc).__name__)
    return new_hash
