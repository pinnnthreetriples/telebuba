"""Re-key an account's session: a login of its own that the seller never had a copy of.

An imported ``.session`` carries an auth key somebody else may have kept. So the account
is signed in again, as a fresh session whose key exists only on this machine: a NEW client
with its own session file exports a login token (``auth.exportLoginToken``), the imported
session accepts it (``auth.acceptLoginToken``), the cloud password finishes the login when
Telegram asks for it, and the new session must be the SAME Telegram user. The account's
stored device fingerprint and proxy are reused, so Telegram sees the same device.

Nothing is kept unless everything succeeds, in this order:

1. the new session is built under a scratch name, never over a file that exists;
2. it signs in and is checked against the imported one's user;
3. the files are swapped (the imported one is set aside, not deleted), the new one is
   opened once as the account's own and must answer as the same user;
4. only then is the imported file deleted.

Any failure before 4 puts the imported file back and ends the new authorization, and the
account is exactly as it was. The old AUTHORIZATION (the seller-known key) is ended by the
service afterwards, with :func:`core.telegram_client._session_close.close_sessions`: its
hash is returned here. Every failure is a bounded code, never Telethon prose. The cloud
password is only passed to Telegram, never logged or returned.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from anyio import Path as AsyncPath
from telethon import errors
from telethon.tl.functions.account import ChangeAuthorizationSettingsRequest
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.config import settings
from core.telegram_client._auth import _auth_lock, _remove_session_file
from core.telegram_client._client import (
    create_telegram_client,
    prepare_telegram_client_profile,
)
from core.telegram_client._pool import TelegramClientPoolError, get_client, removing_client
from core.telegram_client._profile import _DEAD_SESSION_ERRORS, _dead_session_code
from core.telegram_client._session_audit import _listing, _own_hash
from schemas.device_fingerprint import TelegramClientRequest
from schemas.session_rekey import RekeyRefusalCode, RekeyResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.device_fingerprint import DeviceType, TelegramClientProfile

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)

_APPROVER: RekeyRefusalCode = "rekey_approver_unavailable"
_TOKEN: RekeyRefusalCode = "rekey_token_rejected"  # noqa: S105 - a refusal code
_TWOFA_MISSING: RekeyRefusalCode = "rekey_twofa_missing"
_TWOFA_INVALID: RekeyRefusalCode = "rekey_twofa_invalid"
_MISMATCH: RekeyRefusalCode = "rekey_user_mismatch"
_CONFLICT: RekeyRefusalCode = "rekey_session_conflict"
_SWAP: RekeyRefusalCode = "rekey_swap_failed"
_FAILED: RekeyRefusalCode = "rekey_failed"
_FLOOD = "flood_wait"
_UNAVAILABLE = "unavailable"
# The suffix of the file the imported session is set aside as while the swap is verified.
_ASIDE_SUFFIX = ".rekey-aside"


class RekeyGatewayError(Exception):
    """The re-key was refused or failed; ``str(exc)`` is the stable code, never Telethon prose."""

    def __init__(
        self,
        code: str,
        *,
        retry_after_seconds: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.error_type = error_type


def _refusal(code: str, error_type: str | None = None) -> RekeyGatewayError:
    return RekeyGatewayError(code, error_type=error_type)


# Telegram answers that end the attempt, each with the code the operator is shown.
_ERROR_CODES: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((errors.PasswordHashInvalidError,), _TWOFA_INVALID),
    (
        (
            errors.AuthTokenExpiredError,
            errors.AuthTokenInvalidxError,
            errors.AuthTokenAlreadyAcceptedError,
        ),
        _TOKEN,
    ),
    ((TelegramClientPoolError, ConnectionError, TimeoutError), _UNAVAILABLE),
)


def _classify(exc: Exception) -> RekeyGatewayError:
    """The bounded code for whatever stopped the login or the swap."""
    if isinstance(exc, RekeyGatewayError):
        return exc
    name = type(exc).__name__
    if isinstance(exc, errors.FloodWaitError):
        return RekeyGatewayError(_FLOOD, retry_after_seconds=exc.seconds, error_type=name)
    if isinstance(exc, _DEAD_SESSION_ERRORS):
        return _refusal(_dead_session_code(exc), name)
    for error_types, code in _ERROR_CODES:
        if isinstance(exc, error_types):
            return _refusal(code, name)
    logger.warning("session re-key failed for an account", exc_info=exc)
    return _refusal(_FAILED, name)


async def _sign_in_by_token(
    new: TelegramClient, approver: TelegramClient, password: str | None
) -> str:
    """Log ``new`` in with a token ``approver`` accepts; the new authorization's hash."""
    await new.connect()
    qr = await new.qr_login()
    accepted = await approver(AcceptLoginTokenRequest(token=qr.token))
    new_hash = getattr(accepted, "hash", None)
    if not isinstance(new_hash, int):
        raise _refusal(_FAILED, "AuthorizationWithoutHash")
    if getattr(accepted, "unconfirmed", None):
        # A login by a session that is old enough to approve it is the owner's own: say so,
        # as an official client does when the user recognises the new session.
        await approver(ChangeAuthorizationSettingsRequest(hash=new_hash, confirmed=True))
    try:
        await qr.wait(timeout=settings.telegram.session_check_timeout_seconds)
    except errors.SessionPasswordNeededError:
        if not password:
            raise _refusal(_TWOFA_MISSING) from None
        await new.sign_in(password=password)
    return str(new_hash)


async def _discard(new: TelegramClient, scratch_path: str, *, signed_in: bool) -> None:
    """Undo a failed attempt: end the new authorization, disconnect, remove its file.

    Each step runs whatever the one before did: a logout or disconnect that raises must not
    leave the half-made ``.session`` behind to refuse every retry.
    """
    steps = (("logout", new.log_out),) if signed_in else ()
    for step, call in (*steps, ("disconnect", new.disconnect)):
        try:
            await call()
        except Exception as exc:  # noqa: BLE001 - best effort; the file still goes below.
            logger.warning("re-key %s failed (%s)", step, type(exc).__name__)
    try:
        await _remove_session_file(scratch_path)
    except OSError as exc:
        logger.warning("re-key scratch session kept (%s)", type(exc).__name__)


async def _require_same_user(client: TelegramClient, user_id: int, code: str) -> None:
    """Refuse (``code``) when ``client`` is not the Telegram user ``user_id``."""
    me = await client.get_me()
    if getattr(me, "id", None) != user_id:
        raise _refusal(code)


async def _approver_identity(approver: TelegramClient) -> tuple[str, int]:
    """The imported session's own authorization hash and Telegram user id."""
    old_hash = await _current_hash(approver)
    me = await approver.get_me()
    user_id = getattr(me, "id", None)
    if not isinstance(user_id, int):
        raise _refusal(_APPROVER)
    return old_hash, user_id


async def _require_free(session_path: str) -> None:
    """Refuse to sign in over a file that is already there: an orphan, or another account's."""
    if await AsyncPath(f"{session_path}.session").exists():
        raise _refusal(_CONFLICT)


async def _swap(
    profile: TelegramClientProfile, scratch_path: str, user_id: int, device: DeviceType
) -> None:
    """Put the scratch file in the imported file's place, or leave everything as it was.

    The imported file is set aside, not deleted, until the new one has answered as the same
    user from its final place; a failure moves it back.
    """
    account_id = profile.account_id
    final = AsyncPath(f"{profile.session_path}.session")
    aside = AsyncPath(f"{profile.session_path}.session{_ASIDE_SUFFIX}")
    fresh = AsyncPath(f"{scratch_path}.session")
    async with removing_client(account_id, device):
        await asyncio.to_thread(os.replace, str(final), str(aside))
        try:
            await asyncio.to_thread(os.replace, str(fresh), str(final))
            check = create_telegram_client(profile)
            try:
                await check.connect()
                await _require_same_user(check, user_id, _SWAP)
            finally:
                await check.disconnect()
        except BaseException:
            # Back to the imported file, and the new authorization is ended by the caller.
            if await final.exists():
                await asyncio.to_thread(os.replace, str(final), str(fresh))
            await asyncio.to_thread(os.replace, str(aside), str(final))
            raise
        await aside.unlink()


async def _current_hash(approver: TelegramClient) -> str:
    authorizations, _ttl = await _listing(approver)
    return str(_own_hash(authorizations))


async def rekey_session(
    account_id: str, device: DeviceType, scratch_name: str, password: str | None
) -> RekeyResult:
    """Replace ``device``'s session of the account with a login of its own; see the module doc.

    Raises :class:`RekeyGatewayError` and never a Telethon error. On success the imported
    authorization is still standing: its hash is ``old_hash``, to be ended by the caller.
    """
    async with _auth_lock(account_id):
        try:
            approver = await get_client(account_id, device)
            old_hash, user_id = await _approver_identity(approver)
            scratch = await prepare_telegram_client_profile(
                TelegramClientRequest(
                    account_id=account_id,
                    device_type=device,
                    session_name=scratch_name,
                    # The login token arrives as an update, so this client must receive them.
                    receive_updates=True,
                ),
            )
            await _require_free(scratch.session_path)
            new = create_telegram_client(scratch)
            signed_in = False
            try:
                new_hash = await _sign_in_by_token(new, approver, password)
                signed_in = True
                await _require_same_user(new, user_id, _MISMATCH)
                await new.disconnect()
                final = await prepare_telegram_client_profile(
                    TelegramClientRequest(
                        account_id=account_id, device_type=device, receive_updates=False
                    ),
                )
                await _swap(final, scratch.session_path, user_id, device)
            except BaseException:
                await asyncio.shield(_discard(new, scratch.session_path, signed_in=signed_in))
                raise
        except Exception as exc:
            raise _classify(exc) from exc
    return RekeyResult(old_hash=old_hash, new_hash=new_hash)
