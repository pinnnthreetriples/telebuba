"""Phone-code login + logout RPCs — the only place SendCode/SignIn/log_out live.

Re-authorises an existing account by phone code. ``request_phone_code`` connects
the account's session and asks Telegram to send a login code (returning the
``phone_code_hash``); ``submit_phone_code`` reconnects the *same* session and
completes the sign-in (handling 2FA). ``log_out_session`` logs the account out
server-side, optionally wiping the local ``.session`` token.

Every Telethon failure is classified into a typed result/challenge here so the
``services`` layer never imports ``telethon`` (layer isolation, non-negotiable #5).

All three flows build their own client instead of borrowing from the pool (they
need a connection they may sign in on or revoke), and all three therefore run it
under ``removing_client``: ``check_telegram_session`` pools a client for *every*
account — new and unauthorized ones included — and nothing expires that entry,
so the ``.session`` file is very likely already open elsewhere. Two
``SQLiteSession`` handles on one file mean ``database is locked`` on the second
writer, and worse, the second reader sees a stale ``auth_key`` until the first
commits — it can act on the credential the other handle just replaced. The
tombstone disconnects the pooled twin and refuses rebuilds until the flow ends.

The tombstone is not enough on its own: it refuses POOL rebuilds, so it does not
fence these three flows against EACH OTHER, and each of them builds its own
client. Measured: a logout started while a request-code was in flight put two
Telethon clients on one ``.session`` file at once — the exact hazard above. The
SPA can produce that (logout during a code submit, two browser tabs, request-code
followed straight by reset-session), so all three also run under
:func:`_auth_lock`, one lock per account.
"""

from __future__ import annotations

import asyncio
from pathlib import Path as PlainPath
from typing import TYPE_CHECKING

from anyio import Path, to_thread
from telethon import TelegramClient, errors
from telethon.tl.functions.auth import LogOutRequest

from core.config import settings
from core.session_vault import quarantine_file, snapshot_session
from core.telegram_client._api_credentials import record_key_api
from core.telegram_client._auth_bounds import (
    UNREACHABLE_ERRORS,
    auth_deadline,
    request_failure_code,
    safe_disconnect,
)
from core.telegram_client._client import (
    _session_path,
    create_telegram_client,
    prepare_telegram_client_profile,
)
from core.telegram_client._pool import removing_client
from core.telegram_client._profile import _DEAD_SESSION_ERRORS
from core.telegram_client._twofa_candidates import sign_in_with_passwords
from core.telegram_client._util import optional_bool, optional_str
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeChallenge, PhoneCodeRequest, PhoneCodeSubmit
from schemas.telegram_session import TelegramSessionCheckResult

if TYPE_CHECKING:
    from schemas.device_fingerprint import TelegramClientProfile
    from schemas.telegram_session import SessionCheckStatus


# One lock per account, created lazily and never freed — bounded by the number of
# accounts that have ever logged in or out, the same shape as
# ``_pool._CONNECT_LOCKS`` and ``services.accounts._import_locks``. That module is
# not reused because ``core`` may not import ``services`` (layer isolation,
# non-negotiable #5).
_AUTH_LOCKS: dict[str, asyncio.Lock] = {}


def account_auth_lock(account_id: str) -> asyncio.Lock:
    """The lock every sign-in / sign-out of ``account_id`` holds, for account removal.

    Removal takes it so no second-device sign-in can record a session between its
    read of the account and its delete. Taken outside ``removing_client`` like here.
    """
    return _auth_lock(account_id)


def _auth_lock(account_id: str) -> asyncio.Lock:
    """Serialise this module's three flows for one account.

    Always taken OUTSIDE ``removing_client``, which holds no lock across its
    ``yield``, so the two cannot deadlock in either order.
    """
    lock = _AUTH_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _AUTH_LOCKS[account_id] = lock
    return lock


def _login_request(
    account_id: str, session_name: str | None, *, new_key: bool = False
) -> TelegramClientRequest:
    return TelegramClientRequest(
        account_id=account_id,
        session_name=session_name,
        receive_updates=False,
        new_key=new_key,
    )


async def request_phone_code(request: PhoneCodeRequest) -> PhoneCodeChallenge:
    """Ask Telegram to send a login code to ``request.phone`` on its own session."""
    profile = await prepare_telegram_client_profile(
        _login_request(request.account_id, request.session_name, new_key=request.new_key),
    )
    async with (
        _auth_lock(request.account_id),
        removing_client(request.account_id, profile.device_type),
    ):
        client = create_telegram_client(profile)
        try:
            # One deadline over connect + SendCode: nothing inside bounds itself.
            async with asyncio.timeout(auth_deadline()):
                await client.connect()
                sent = await client.send_code_request(request.phone)
        except errors.FloodWaitError as exc:
            return _challenge_error(
                request,
                f"flood wait {exc.seconds}s",
                code="flood_wait",
                flood_wait_seconds=exc.seconds,
            )
        except Exception as exc:  # noqa: BLE001 - classify any SDK/network failure for the UI.
            # Class name only, never ``str(exc)``: this arm catches transport
            # failures, and a proxy error stringifies with the proxy
            # ``user:pass@host:port``, which the API renders verbatim in the
            # operator's browser (non-negotiable #12, same call ``_read`` makes).
            return _challenge_error(request, type(exc).__name__, code=request_failure_code(exc))
        finally:
            await safe_disconnect(client)
    return PhoneCodeChallenge(
        account_id=request.account_id,
        phone=request.phone,
        phone_code_hash=optional_str(getattr(sent, "phone_code_hash", None)) or "",
    )


async def submit_phone_code(request: PhoneCodeSubmit) -> TelegramSessionCheckResult:
    """Complete sign-in with the code (+ 2FA password); return the session verdict.

    Code and password are completed on one connection — a 2FA account therefore
    needs both supplied in the same submit (the design's session card has both
    fields). A submit with only a code on a 2FA account returns ``unauthorized``.
    """
    profile = await prepare_telegram_client_profile(
        _login_request(request.account_id, request.session_name, new_key=request.new_key),
    )
    async with (
        _auth_lock(request.account_id),
        removing_client(request.account_id, profile.device_type),
    ):
        client = create_telegram_client(profile)
        attempted: list[str] = []
        try:
            async with asyncio.timeout(auth_deadline()):
                await client.connect()
                typed_accepted = await _sign_in(client, request, attempted)
                # The key now carries this API for good (a new one, when it was a new key).
                await record_key_api(profile)
                return _alive_result(
                    profile, await client.get_me(), typed_password_accepted=typed_accepted
                )
        except (errors.SessionPasswordNeededError, *_SIGN_IN_ERRORS) as exc:
            return _error_result(profile, exc, status="unauthorized")
        except errors.FloodWaitError as exc:
            return _error_result(
                profile,
                exc,
                status="flood_wait",
                is_temporary=True,
                flood_wait_seconds=exc.seconds,
            )
        except UNREACHABLE_ERRORS as exc:
            # Deadline expiry (``TimeoutError``), a dead proxy or a dropped socket:
            # nothing was learned about the account, so it is a temporary fault, unless
            # the deadline fired AFTER Telegram accepted the sign-in.
            # Alive after a password step with ONLY the typed password sent can only mean
            # that one was accepted; once a fallback was tried it could be either.
            typed_only = request.password is not None and attempted == [request.password]
            if isinstance(exc, TimeoutError) and (
                alive := await _probe_authorized(client, profile, typed_accepted=typed_only)
            ):
                await record_key_api(profile)
                return alive
            return _error_result(profile, exc, status="network_error", is_temporary=True)
        except Exception as exc:  # noqa: BLE001 - classify any other SDK/network failure for the UI.
            # PhoneNumberBanned / AuthRestart / connect() ConnectionError etc. would
            # otherwise escape raw; its siblings all classify, so match that contract.
            return _error_result(profile, exc, status="unknown_error", is_temporary=True)
        finally:
            await safe_disconnect(client)


async def _probe_authorized(
    client: TelegramClient, profile: TelegramClientProfile, *, typed_accepted: bool = False
) -> TelegramSessionCheckResult | None:
    """Alive verdict when a sign-in the deadline cut short had in fact been accepted.

    The deadline can fire after ``sign_in`` landed and before ``get_me`` answered: the
    ``.session`` is then authorized while the verdict would say ``network_error``. A short,
    separately bounded probe tells the two apart; any failure of the probe is ``None``.
    """
    try:
        async with asyncio.timeout(settings.telegram.signin_probe_timeout_seconds):
            await client.connect()
            if not await client.is_user_authorized():
                return None
            return _alive_result(
                profile, await client.get_me(), typed_password_accepted=typed_accepted
            )
    except Exception:  # noqa: BLE001 - a probe that fails proves nothing; keep the original verdict.
        return None


async def _sign_in(
    client: TelegramClient, request: PhoneCodeSubmit, attempted: list[str] | None = None
) -> bool:
    """Sign in; ``True`` when Telegram accepted the password the operator TYPED."""
    try:
        await client.sign_in(
            phone=request.phone,
            code=request.code,
            phone_code_hash=request.phone_code_hash,
        )
    except errors.SessionPasswordNeededError:
        passwords = [*([request.password] if request.password else []), *request.fallback_passwords]
        if not passwords:
            raise
        accepted = await sign_in_with_passwords(client, request.account_id, passwords, attempted)
        return request.password is not None and accepted == request.password
    return False


async def log_out_session(
    request: TelegramClientRequest,
    *,
    wipe_session: bool = False,
) -> TelegramSessionCheckResult:
    """End the session's authorization server-side; with ``wipe_session`` remove the local file.

    Ended only on proof: Telegram answered ``auth.logOut``, or refused because the key is
    already dead (revoked, unregistered, the account deactivated). Then the account is
    ``unauthorized`` and, with ``wipe_session``, its file is removed. Anything else (a
    timeout, a dropped proxy, another refusal) proves nothing about the key, which may still
    be alive at Telegram: the result is a temporary ``network_error`` naming the exception
    class, and the file stays whatever the flag says. Deleting it then would throw away the
    only copy of a live key (one the seller may also hold).

    The call is ``auth.logOut`` itself, not Telethon's ``log_out()``: that one answers
    ``False`` for every refusal, a dead key included, so it cannot tell "already gone" from
    "not ended", and it deletes the file on its own.

    The whole call runs under the pool tombstone, wipe or not. It evicts the pooled client so
    the ``.session`` file is not held open (on Windows that handle is what makes our delete
    fail), and it drops the cached client whose ``auth_key`` this logout is about to revoke,
    instead of leaving a borrower holding a dead credential. The ``_auth`` lock on top is
    what keeps a code submit from holding a second handle on the same file while this logout
    revokes the key.
    """
    profile = await prepare_telegram_client_profile(request)
    async with (
        _auth_lock(request.account_id),
        removing_client(request.account_id, profile.device_type),
    ):
        client = create_telegram_client(profile)
        try:
            async with asyncio.timeout(auth_deadline()):
                await client.connect()
                await _end_authorization(client)
        except Exception as exc:  # noqa: BLE001 - not ended; record the class only.
            # Class name, not ``str(exc)``: this arm catches transport failures, and a
            # ``python_socks`` error stringifies with the proxy ``user:pass@host:port``.
            return _error_result(profile, exc, status="network_error", is_temporary=True)
        finally:
            await safe_disconnect(client)
        if wipe_session:
            await _remove_session_file(profile.session_path)
    return _status_result(profile, status="unauthorized")


async def _end_authorization(client: TelegramClient) -> None:
    """``auth.logOut``; a key that is already dead counts as ended. Anything else raises."""
    try:
        await client(LogOutRequest())
    except _DEAD_SESSION_ERRORS:
        return


_SIGN_IN_ERRORS = (
    errors.PhoneCodeInvalidError,
    errors.PhoneCodeExpiredError,
    errors.PhoneCodeEmptyError,
    errors.PhoneNumberInvalidError,
    errors.PasswordHashInvalidError,
)


def _challenge_error(
    request: PhoneCodeRequest,
    message: str,
    *,
    code: str,
    flood_wait_seconds: int | None = None,
) -> PhoneCodeChallenge:
    return PhoneCodeChallenge(
        account_id=request.account_id,
        phone=request.phone,
        error=message,
        error_code=code,
        flood_wait_seconds=flood_wait_seconds,
    )


async def _remove_session_file(session_path: str) -> None:
    """Take the session file out of the sessions folder: into the quarantine, not deleted.

    A ``.session`` is the only copy of an auth key; a removal that turns out wrong (the
    wrong account, a key that was still alive) must stay recoverable for a while
    (``core.session_vault``, purged after ``SESSION_VAULT__QUARANTINE_DAYS``). Telethon's
    SQLiteSession stores at "<session_path>.session"; its ``-journal`` sidecar, if one is
    left, holds nothing of its own and is dropped.
    """
    session_file = Path(f"{session_path}.session")
    if await session_file.exists():
        await to_thread.run_sync(quarantine_file, PlainPath(str(session_file)))
    journal = Path(f"{session_path}.session-journal")
    if await journal.exists():
        await journal.unlink()


async def remove_account_session(account_id: str, session_name: str | None = None) -> None:
    """Unlink an account's Telethon ``.session`` file from disk.

    Path composition (and the ``session_name`` → stored-name → ``account_id``
    resolution) is shared with client construction via ``_session_path``, so
    ``services`` callers never re-derive the session-dir layout — they just name
    the account. Callers pass the name off the account row, so the resolution
    lands on the explicit branch and does not depend on the row still existing.
    """
    await _remove_session_file(await _session_path(_login_request(account_id, session_name)))


def _status_result(
    profile: TelegramClientProfile,
    *,
    status: SessionCheckStatus,
    is_temporary: bool = False,
    error_message: str | None = None,
) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status=status,
        is_temporary=is_temporary,
        error_message=error_message,
    )


def _error_result(
    profile: TelegramClientProfile,
    exc: Exception,
    *,
    status: SessionCheckStatus,
    is_temporary: bool = False,
    flood_wait_seconds: int | None = None,
) -> TelegramSessionCheckResult:
    """Typed failure verdict for a submit — bounded, content-free ``error_message``.

    The message is the exception's class name, never ``str(exc)``: the caller
    surfaces it as the HTTP 400 detail, and ``submit_phone_code``'s catch-all arm
    is where transport failures land — a ``python_socks`` proxy error stringifies
    with ``user:pass@host:port`` and a session fault with the ``.session`` path.
    Bounded class names only, exactly like ``_read``'s pool/socket arm and
    ``services.accounts.privacy``'s sweep (non-negotiable #12).
    """
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status=status,
        is_temporary=is_temporary,
        error_type=type(exc).__name__,
        error_message=type(exc).__name__,
        flood_wait_seconds=flood_wait_seconds,
    )


def _alive_result(
    profile: TelegramClientProfile, me: object, *, typed_password_accepted: bool = False
) -> TelegramSessionCheckResult:
    user_id = getattr(me, "id", None)
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status="alive",
        is_temporary=False,
        user_id=user_id if isinstance(user_id, int) else None,
        phone=optional_str(getattr(me, "phone", None)),
        username=optional_str(getattr(me, "username", None)),
        first_name=optional_str(getattr(me, "first_name", None)),
        last_name=optional_str(getattr(me, "last_name", None)),
        premium=optional_bool(getattr(me, "premium", None)),
        typed_password_accepted=typed_password_accepted,
    )


async def back_up_account_session(account_id: str, session_name: str | None = None) -> bool:
    """Store an encrypted copy of one of the account's session files (``core.session_vault``).

    ``True`` when a new version was written; ``False`` when the vault is off, the file is not
    there or nothing changed since the last copy. Path composition is the client's own, so
    the copy is of exactly the file a client would open.
    """
    session_file = PlainPath(
        f"{await _session_path(_login_request(account_id, session_name))}.session"
    )
    return await to_thread.run_sync(snapshot_session, session_file) is not None
