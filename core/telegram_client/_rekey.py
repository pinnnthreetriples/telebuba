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
3. the new session's content is copied into the imported file's place, in place (the imported
   content is kept aside, the file itself is never renamed: see ``_files.copy_session``), and the
   result is opened once as the account's own and must answer as the same user;
4. only then is the imported content dropped.

Any failure before 4 copies the imported content back and ends the new authorization, and
the account is exactly as it was. Once the new content has answered from the account's own
file the swap is DONE: tidying side copies is only logged, since undoing then would end the
key that file now holds. The undo never ends the new authorization while the account's file
may hold its key; a failed copy-back keeps both keys and the side copy (``rekey_incomplete``).
The old AUTHORIZATION (the seller-known key) is ended later, by the service
(``close_sessions``), with the hash returned here. The asking session lists itself as
``hash=0``, so the new session names it: the one entry in its list the approver's lacked
(created when the approver's entry says). Anything but exactly one is a refusal, never ``0``.
Every failure is a bounded code; the cloud password is only passed to Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from anyio import Path as AsyncPath
from telethon import errors
from telethon.tl.functions.account import ChangeAuthorizationSettingsRequest
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.config import settings
from core.telegram_client import _rekey_files as _files
from core.telegram_client._auth import _auth_lock, _remove_session_file
from core.telegram_client._client import (
    create_telegram_client,
    prepare_telegram_client_profile,
)
from core.telegram_client._pool import get_client, removing_client
from core.telegram_client._rekey_errors import (
    _APPROVER,
    _CONFLICT,
    _FAILED,
    _INCOMPLETE,
    _MISMATCH,
    _OLD_UNKNOWN,
    _SWAP,
    _TWOFA_MISSING,
    RekeyGatewayError,
    _classify,
    _refusal,
)
from core.telegram_client._session_audit import _listing
from core.telegram_client._twofa_candidates import sign_in_with_passwords
from schemas.device_fingerprint import TelegramClientRequest
from schemas.session_rekey import RekeyResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telethon import TelegramClient

    from schemas.device_fingerprint import DeviceType, TelegramClientProfile

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)


async def _sign_in_by_token(
    new: TelegramClient, approver: TelegramClient, account_id: str, passwords: Sequence[str]
) -> str:
    """Log ``new`` in with a token ``approver`` accepts; the new authorization's hash."""
    await new.connect()
    qr = await new.qr_login()
    # Telegram answers the acceptance with an update that ``qr.wait`` listens for only once
    # it runs: start waiting BEFORE accepting, or the update lands unheard and the login
    # times out with the token already accepted.
    timeout = settings.telegram.session_check_timeout_seconds
    waiting = asyncio.ensure_future(qr.wait(timeout=timeout))
    await asyncio.sleep(0)
    try:
        new_hash = await _accept_token(approver, qr.token)
    except BaseException:
        waiting.cancel()
        raise
    try:
        await waiting
    except errors.SessionPasswordNeededError:
        if not passwords:
            raise _refusal(_TWOFA_MISSING) from None
        await sign_in_with_passwords(new, account_id, passwords)
    return str(new_hash)


async def _accept_token(approver: TelegramClient, token: bytes) -> int:
    """Have the imported session accept ``token``; the new authorization's hash."""
    accepted = await approver(AcceptLoginTokenRequest(token=token))
    new_hash = getattr(accepted, "hash", None)
    if not isinstance(new_hash, int):
        raise _refusal(_FAILED, "AuthorizationWithoutHash")
    if getattr(accepted, "unconfirmed", None):
        # A login by a session that is old enough to approve it is the owner's own: say so,
        # as an official client does when the user recognises the new session.
        await approver(ChangeAuthorizationSettingsRequest(hash=new_hash, confirmed=True))
    return new_hash


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


async def _discard(
    new: TelegramClient, scratch_path: str, *, signed_in: bool, final_file: str | None = None
) -> None:
    """Undo a failed attempt: end the new authorization, disconnect, remove its file.

    Each step runs whatever the one before did: a logout or disconnect that raises must not
    leave the half-made ``.session`` behind to refuse every retry.

    ``final_file`` is the account's own file once the swap may have written it. The new
    authorization is then ended only on proof that this file does not hold its key: ending
    it otherwise could end the account's only working key. Kept, it stays signed in with its
    scratch file in place, and the warning says so.
    """
    if signed_in and final_file is not None and await _may_hold_new_key(new, final_file):
        logger.warning("re-key undo kept the new authorization: the account's file may hold it")
        await _disconnect_quietly(new)
        return
    if signed_in and not new.is_connected():
        # The swap stage disconnects ``new`` first; ``log_out`` on a closed client would
        # fail and leave the new authorization standing, orphaned with no session file.
        try:
            await asyncio.wait_for(new.connect(), _RECONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - the logout below then fails too; logged.
            logger.warning("re-key reconnect failed (%s)", type(exc).__name__)
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


@dataclass(frozen=True)
class _Approver:
    """What the imported session knew before the new login: its user and what it lists."""

    user_id: int
    hashes: frozenset[int]
    created: datetime | None


def _real_hashes(authorizations: Sequence[object]) -> dict[int, object]:
    """The other sessions' entries by real hash.

    ``0`` is "whoever asks", never a key of anything, and the asking session's own entry
    (``current``) is not "another session" whatever hash it carries.
    """
    found: dict[int, object] = {}
    for authorization in authorizations:
        hash_ = getattr(authorization, "hash", None)
        if isinstance(hash_, int) and hash_ != 0 and not getattr(authorization, "current", None):
            found[hash_] = authorization
    return found


async def _approver_identity(approver: TelegramClient) -> _Approver:
    """The imported session's Telegram user id and the authorizations it lists."""
    authorizations, _ttl = await _listing(approver)
    current = next((a for a in authorizations if getattr(a, "current", None)), None)
    created = getattr(current, "date_created", None)
    me = await approver.get_me()
    user_id = getattr(me, "id", None)
    if not isinstance(user_id, int):
        raise _refusal(_APPROVER)
    return _Approver(
        user_id=user_id,
        hashes=frozenset(_real_hashes(authorizations)),
        created=created if isinstance(created, datetime) else None,
    )


async def _old_hash(new: TelegramClient, before: _Approver, new_hash: str) -> str:
    """The imported authorization's real hash, seen from the new session; see the module doc.

    Refused when the approver's own creation date is not known: without it a stranger who
    logged in between the two reads could not be told from the imported session.
    """
    authorizations, _ttl = await _listing(new)
    fresh = {
        h: a
        for h, a in _real_hashes(authorizations).items()
        if h not in before.hashes and h != int(new_hash)
    }
    if len(fresh) != 1:
        raise _refusal(_OLD_UNKNOWN, f"Candidates{len(fresh)}")
    ((hash_, entry),) = fresh.items()
    created = getattr(entry, "date_created", None)
    if before.created is None or created != before.created:
        raise _refusal(_OLD_UNKNOWN, "CreatedMismatch")
    return str(hash_)


async def _require_free(session_path: str) -> None:
    """Refuse to sign in over a file that is already there: an orphan, or another account's."""
    if await AsyncPath(f"{session_path}.session").exists():
        raise _refusal(_CONFLICT)


async def _require_no_aside(final_session_path: str) -> None:
    """Refuse while an earlier swap's side copy holds a key the account's file does not.

    That copy may be the only one of an old key (a copy-back that failed); a new swap would
    overwrite it. A side copy holding exactly what the account's file holds is a leftover of
    a swap that finished, and goes.
    """
    final = f"{final_session_path}.session"
    aside = f"{final}{_files.ASIDE_SUFFIX}"
    if not await AsyncPath(aside).exists():
        return
    if not await _files.same_key(aside, final):
        raise _refusal(_CONFLICT, "SideCopyKept")
    await _files.tidy(aside)


async def _disconnect_quietly(client: TelegramClient) -> None:
    try:
        await client.disconnect()
    except Exception as exc:  # noqa: BLE001 - best effort; the authorization is kept on purpose.
        logger.warning("re-key disconnect failed (%s)", type(exc).__name__)


async def _require_works(profile: TelegramClientProfile, user_id: int) -> None:
    """Open the account's own file once: it must answer as ``user_id``."""
    check = create_telegram_client(profile)
    try:
        await check.connect()
        await _require_same_user(check, user_id, _SWAP)
    finally:
        await check.disconnect()


async def _swap(
    profile: TelegramClientProfile, scratch_path: str, user_id: int, device: DeviceType
) -> None:
    """Put the new session's content in the imported file, or leave everything as it was.

    The imported file's content is kept in a side copy, not dropped, until the new one has
    answered as the same user from its final place; a failure copies it back. The final file
    itself is never renamed or deleted, see :func:`_files.copy_session`.

    Once the new content has answered the swap is done, and nothing after it raises: the
    side copies are tidied best effort. A copy-back that fails raises ``rekey_incomplete``
    (the account's file may hold either key) and keeps the side copy, the only one of the
    old key that may be left.
    """
    final = f"{profile.session_path}.session"
    aside = f"{final}{_files.ASIDE_SUFFIX}"
    fresh = f"{scratch_path}.session"
    async with removing_client(profile.account_id, device):
        try:
            await asyncio.to_thread(_files.copy_session, final, aside)
        except (sqlite3.Error, OSError) as exc:
            # The account's file was only read; a half-written side copy is worth nothing.
            await _files.tidy(aside)
            raise _refusal(_SWAP, type(exc).__name__) from exc
        try:
            await asyncio.to_thread(_files.copy_session, fresh, final)
            await _require_works(profile, user_id)
        except BaseException as failure:
            await _copy_back(aside, final, failure)
            if isinstance(failure, (sqlite3.Error, OSError)):
                raise _refusal(_SWAP, type(failure).__name__) from failure
            raise
    # Done: the account's file holds the new key and answered with it.
    await _files.tidy(aside)
    await _files.tidy(fresh)


async def _copy_back(aside: str, final: str, failure: BaseException) -> None:
    """Put the imported content back after ``failure``; ``rekey_incomplete`` if it will not go.

    A cancellation keeps propagating as itself: the caller's undo then finds the account's
    file may hold the new key and keeps the new authorization (:func:`_discard`).
    """
    try:
        await asyncio.to_thread(_files.copy_session, aside, final)
    except (sqlite3.Error, OSError) as exc:
        if await _files.same_key(final, aside):
            # The copy in never landed (SQLite's backup commits whole or not at all): the
            # account's file still holds the imported key, and nothing needs putting back.
            await _files.tidy(aside)
            return
        logger.warning("re-key copy-back failed (%s); the side copy is kept", type(exc).__name__)
        if isinstance(failure, Exception):
            raise _refusal(_INCOMPLETE, type(exc).__name__) from exc
        return
    await _files.tidy(aside)


async def rekey_session(
    account_id: str, device: DeviceType, scratch_name: str, passwords: Sequence[str]
) -> RekeyResult:
    """Replace ``device``'s session of the account with a login of its own; see the module doc.

    ``passwords`` are the 2FA candidates, current first. Raises :class:`RekeyGatewayError`,
    never a Telethon error. On success the imported authorization is still standing: its hash
    is ``old_hash``, to be ended by the caller.
    """
    async with _auth_lock(account_id):
        try:
            approver = await get_client(account_id, device)
            before = await _approver_identity(approver)
            user_id = before.user_id
            scratch = await prepare_telegram_client_profile(
                TelegramClientRequest(
                    account_id=account_id,
                    device_type=device,
                    session_name=scratch_name,
                    # The login token arrives as an update, so this client must receive them.
                    receive_updates=True,
                ),
            )
            final = await prepare_telegram_client_profile(
                TelegramClientRequest(
                    account_id=account_id, device_type=device, receive_updates=False
                ),
            )
            await _require_free(scratch.session_path)
            await _require_no_aside(final.session_path)
            new = create_telegram_client(scratch)
            signed_in = False
            # Set once the swap may have written the account's own file.
            final_file: str | None = None
            old_hash = ""
            try:
                new_hash = await _sign_in_by_token(new, approver, account_id, passwords)
                signed_in = True
                await _require_same_user(new, user_id, _MISMATCH)
                old_hash = await _old_hash(new, before, new_hash)
                await new.disconnect()
                final_file = f"{final.session_path}.session"
                await _swap(final, scratch.session_path, user_id, device)
            except RekeyGatewayError as exc:
                if exc.code == _INCOMPLETE:
                    # Neither key is ended and no file dropped: a person sorts it out.
                    exc.old_hash = old_hash
                    await asyncio.shield(_disconnect_quietly(new))
                    raise
                await asyncio.shield(
                    _discard(new, scratch.session_path, signed_in=signed_in, final_file=final_file)
                )
                raise
            except BaseException:
                await asyncio.shield(
                    _discard(new, scratch.session_path, signed_in=signed_in, final_file=final_file)
                )
                raise
        except Exception as exc:
            raise _classify(exc) from exc
    return RekeyResult(old_hash=old_hash, new_hash=new_hash)
