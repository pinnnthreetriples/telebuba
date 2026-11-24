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

The flow is journaled (``account_key_intents``, see ``_key_intent``): written before the new key
exists, advanced when the login is accepted (from then on the new authorization exists whatever
happens next), when the old hash is known and when the swap landed. The gateway never closes a
successful intent: the service does, once the ``account_rekeys`` row is stored. A crash leaves
the intent open for ``services.accounts._key_repair``, and an undo whose logout Telegram did
not confirm keeps the scratch file and the intent open for the same reason. Every network call
is bounded: one hung call must not leave the account tombstoned until a restart.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from anyio import Path as AsyncPath

from core.repositories.account_key_intents import open_intent
from core.telegram_client import _rekey_files as _files
from core.telegram_client._api_credentials import record_key_api
from core.telegram_client._auth import _auth_lock
from core.telegram_client._auth_bounds import auth_deadline, safe_disconnect
from core.telegram_client._client import (
    create_telegram_client,
    prepare_telegram_client_profile,
)
from core.telegram_client._key_intent import advance_quietly, close_quietly
from core.telegram_client._pool import get_client, removing_client
from core.telegram_client._rekey_errors import (
    _APPROVER,
    _CONFLICT,
    _INCOMPLETE,
    _MISMATCH,
    _OLD_UNKNOWN,
    _SWAP,
    RekeyGatewayError,
    _classify,
    _refusal,
)
from core.telegram_client._rekey_token import _sign_in_by_token
from core.telegram_client._rekey_undo import _discard, _disconnect_quietly
from core.telegram_client._session_audit import _listing
from schemas.device_fingerprint import TelegramClientRequest
from schemas.session_rekey import RekeyResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telethon import TelegramClient

    from schemas.device_fingerprint import DeviceType, TelegramClientProfile

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)


async def _require_same_user(client: TelegramClient, user_id: int, code: str) -> None:
    """Refuse (``code``) when ``client`` is not the Telegram user ``user_id``."""
    async with asyncio.timeout(auth_deadline()):
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
    async with asyncio.timeout(auth_deadline()):
        authorizations, _ttl = await _listing(approver)
        me = await approver.get_me()
    current = next((a for a in authorizations if getattr(a, "current", None)), None)
    created = getattr(current, "date_created", None)
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
    async with asyncio.timeout(auth_deadline()):
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


async def _require_works(profile: TelegramClientProfile, user_id: int) -> None:
    """Open the account's own file once: it must answer as ``user_id``."""
    check = create_telegram_client(profile)
    try:
        async with asyncio.timeout(auth_deadline()):
            await check.connect()
        await _require_same_user(check, user_id, _SWAP)
    finally:
        await safe_disconnect(check)


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
                    # A brand-new key: it may use the new API (recorded after the swap).
                    new_key=True,
                ),
            )
            final = await prepare_telegram_client_profile(
                # The account's own file, opened with the API the NEW key is made with: once
                # swapped in, that is the key it holds.
                TelegramClientRequest(
                    account_id=account_id, device_type=device, receive_updates=False, new_key=True
                ),
            )
            await _require_free(scratch.session_path)
            await _require_no_aside(final.session_path)
            final_file = f"{final.session_path}.session"
            intent_id = await _open_rekey_intent(account_id, device, scratch_name, final)
            try:
                new = create_telegram_client(scratch)
            except BaseException:
                await asyncio.shield(close_quietly(intent_id, "undone"))
                raise
            signed_in = False
            # Set once the swap may have written the account's own file.
            swap_file: str | None = None
            old_hash = ""

            async def accepted(new_hash: str | None) -> None:
                # An authorization exists (or may: ``None``) from here on, whatever follows.
                nonlocal signed_in
                signed_in = True
                await advance_quietly(intent_id, stage="signed_in", new_hash=new_hash)

            try:
                new_hash = await _sign_in_by_token(new, approver, account_id, passwords, accepted)
                await _require_same_user(new, user_id, _MISMATCH)
                old_hash = await _old_hash(new, before, new_hash)
                await advance_quietly(intent_id, old_hash=old_hash)
                await safe_disconnect(new)
                swap_file = final_file
                await _swap(final, scratch.session_path, user_id, device)
                await advance_quietly(intent_id, stage="swapped")
                await _record_final_api(final)
            except RekeyGatewayError as exc:
                if exc.code == _INCOMPLETE:
                    # Neither key is ended and no file dropped: a person sorts it out.
                    exc.old_hash = old_hash
                    await asyncio.shield(_disconnect_quietly(new))
                    await asyncio.shield(close_quietly(intent_id, "incomplete"))
                    raise
                await asyncio.shield(
                    _discard(
                        new,
                        scratch.session_path,
                        signed_in=signed_in,
                        final_file=swap_file,
                        intent_id=intent_id,
                    )
                )
                raise
            except BaseException:
                await asyncio.shield(
                    _discard(
                        new,
                        scratch.session_path,
                        signed_in=signed_in,
                        final_file=swap_file,
                        intent_id=intent_id,
                    )
                )
                raise
        except Exception as exc:
            raise _classify(exc) from exc
    return RekeyResult(old_hash=old_hash, new_hash=new_hash, intent_id=intent_id)


async def _open_rekey_intent(
    account_id: str, device: DeviceType, scratch_name: str, final: TelegramClientProfile
) -> int:
    """Journal the re-key before the new key exists; what the repair needs after a crash."""
    mark = await asyncio.to_thread(_files.key_mark, f"{final.session_path}.session")
    return await open_intent(
        account_id=account_id,
        kind="rekey",
        device_type=device,
        session_name=scratch_name,
        api_id=getattr(getattr(final, "device", None), "api_id", None),
        old_key_mark=mark,
    )


async def _record_final_api(final: TelegramClientProfile) -> None:
    """The account's file holds the new key now: it keeps the API that made it.

    Never fails the re-key: the swap is done, and ending it over a bookkeeping write would
    undo nothing. The intent keeps the API id, and the service records it when it finishes.
    """
    try:
        await record_key_api(final)
    except Exception as exc:  # noqa: BLE001 - see above; the class is the only note.
        logger.warning("re-key could not record the key's API (%s)", type(exc).__name__)
