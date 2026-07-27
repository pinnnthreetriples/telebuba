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
from typing import TYPE_CHECKING

from anyio import Path
from telethon import TelegramClient, errors

from core.telegram_client._client import (
    _session_path,
    create_telegram_client,
    prepare_telegram_client_profile,
)
from core.telegram_client._pool import removing_client
from core.telegram_client._util import optional_str
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


def _login_request(account_id: str, session_name: str | None) -> TelegramClientRequest:
    return TelegramClientRequest(
        account_id=account_id,
        session_name=session_name,
        receive_updates=False,
    )


async def request_phone_code(request: PhoneCodeRequest) -> PhoneCodeChallenge:
    """Ask Telegram to send a login code to ``request.phone`` on its own session."""
    profile = await prepare_telegram_client_profile(
        _login_request(request.account_id, request.session_name),
    )
    async with _auth_lock(request.account_id), removing_client(request.account_id):
        client = create_telegram_client(profile)
        try:
            await client.connect()
            sent = await client.send_code_request(request.phone)
        except errors.FloodWaitError as exc:
            return _challenge_error(request, f"flood wait {exc.seconds}s")
        except Exception as exc:  # noqa: BLE001 - classify any SDK/network failure for the UI.
            # Class name only, never ``str(exc)``: this arm catches transport
            # failures, and a proxy error stringifies with the proxy
            # ``user:pass@host:port``, which the API renders verbatim in the
            # operator's browser (non-negotiable #12, same call ``_read`` makes).
            return _challenge_error(request, type(exc).__name__)
        finally:
            await client.disconnect()
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
        _login_request(request.account_id, request.session_name),
    )
    async with _auth_lock(request.account_id), removing_client(request.account_id):
        client = create_telegram_client(profile)
        try:
            await client.connect()
            await _sign_in(client, request)
            return _alive_result(profile, await client.get_me())
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
        except Exception as exc:  # noqa: BLE001 - classify any other SDK/network failure for the UI.
            # PhoneNumberBanned / AuthRestart / connect() ConnectionError etc. would
            # otherwise escape raw; its siblings all classify, so match that contract.
            return _error_result(profile, exc, status="unknown_error", is_temporary=True)
        finally:
            await client.disconnect()


async def _sign_in(client: TelegramClient, request: PhoneCodeSubmit) -> None:
    try:
        await client.sign_in(
            phone=request.phone,
            code=request.code,
            phone_code_hash=request.phone_code_hash,
        )
    except errors.SessionPasswordNeededError:
        if not request.password:
            raise
        await client.sign_in(password=request.password)


async def log_out_session(
    request: TelegramClientRequest,
    *,
    wipe_session: bool = False,
) -> TelegramSessionCheckResult:
    """Log the account out server-side; with ``wipe_session`` remove the local file.

    Best-effort: even if the server-side ``log_out`` fails, the account is
    reported ``unauthorized`` (that is the operator's intent).

    ``wipe_session`` does not decide *whether* the ``.session`` file survives —
    Telethon's own ``log_out()`` ends with ``session.delete()`` (an ``os.remove``
    whose ``OSError`` it swallows), so a successful plain logout already removes
    the file on POSIX, and on Windows only fails to because a live handle blocks
    it. What the flag decides is whether the removal is *guaranteed*: a logout
    that never reached the server leaves a stale token behind, and wiping it
    makes the next connect mint a fresh auth key.

    The whole call runs under the pool tombstone, wipe or not. It evicts the
    pooled client so the ``.session`` file is not held open (on Windows that
    handle is what makes Telethon's own delete — and ours — fail), and it drops
    the cached client whose ``auth_key`` this logout is about to revoke, instead
    of leaving a borrower holding a dead credential. The ``_auth`` lock on top is
    what keeps a code submit from holding a second handle on the same file while
    this logout revokes the key.
    """
    profile = await prepare_telegram_client_profile(request)
    error_message: str | None = None
    async with _auth_lock(request.account_id), removing_client(request.account_id):
        client = create_telegram_client(profile)
        try:
            await client.connect()
            await client.log_out()
        except Exception as exc:  # noqa: BLE001 - best-effort logout; record the class only.
            # Class name, not ``str(exc)`` — the same contract ``_error_result``
            # states two functions down, and for the same reason: this arm catches
            # transport failures, and a ``python_socks`` error stringifies with the
            # proxy ``user:pass@host:port`` (non-negotiable #12).
            #
            # Nothing reads it today. ``services.accounts.login._end_session`` hands
            # the result to ``update_account_from_session_check``, which writes only
            # ``status`` / ``last_checked_at`` / ``updated_at`` — the ``error_message``
            # a previous comment claimed to "surface" goes nowhere. It stays on the
            # returned model because its sibling field IS read (``login`` raises
            # ``PhoneLoginError(result.error_message)`` off the submit path), so the
            # value a future reader picks up must already be bounded.
            error_message = type(exc).__name__
        finally:
            await client.disconnect()
        if wipe_session:
            await _remove_session_file(profile.session_path)
    return _status_result(profile, status="unauthorized", error_message=error_message)


_SIGN_IN_ERRORS = (
    errors.PhoneCodeInvalidError,
    errors.PhoneCodeExpiredError,
    errors.PhoneCodeEmptyError,
    errors.PhoneNumberInvalidError,
    errors.PasswordHashInvalidError,
)


def _challenge_error(request: PhoneCodeRequest, message: str) -> PhoneCodeChallenge:
    return PhoneCodeChallenge(account_id=request.account_id, phone=request.phone, error=message)


async def _remove_session_file(session_path: str) -> None:
    # Telethon's SQLiteSession stores at "<session_path>.session".
    session_file = Path(f"{session_path}.session")
    if await session_file.exists():
        await session_file.unlink()


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


def _alive_result(profile: TelegramClientProfile, me: object) -> TelegramSessionCheckResult:
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
    )
