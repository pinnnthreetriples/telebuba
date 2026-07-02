"""Phone-code login + logout RPCs — the only place SendCode/SignIn/log_out live.

Re-authorises an existing account by phone code. ``request_phone_code`` connects
the account's session and asks Telegram to send a login code (returning the
``phone_code_hash``); ``submit_phone_code`` reconnects the *same* session and
completes the sign-in (handling 2FA). ``log_out_session`` logs the account out
server-side, optionally wiping the local ``.session`` token.

Every Telethon failure is classified into a typed result/challenge here so the
``services`` layer never imports ``telethon`` (layer isolation, non-negotiable #5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anyio import Path
from telethon import TelegramClient, errors

from core.telegram_client._client import create_telegram_client, prepare_telegram_client_profile
from core.telegram_client._pool import evict_client
from core.telegram_client._util import optional_str
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeChallenge, PhoneCodeRequest, PhoneCodeSubmit
from schemas.telegram_session import TelegramSessionCheckResult

if TYPE_CHECKING:
    from schemas.device_fingerprint import TelegramClientProfile
    from schemas.telegram_session import SessionCheckStatus


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
    client = create_telegram_client(profile)
    try:
        await client.connect()
        sent = await client.send_code_request(request.phone)
    except errors.FloodWaitError as exc:
        return _challenge_error(request, f"flood wait {exc.seconds}s")
    except Exception as exc:  # noqa: BLE001 - classify any SDK/network failure for the UI.
        return _challenge_error(request, str(exc))
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
    reported ``unauthorized`` (that is the operator's intent), and when wiping a
    stale token the ``.session`` file is removed so the next connect mints a
    fresh auth key.
    """
    profile = await prepare_telegram_client_profile(request)
    client = create_telegram_client(profile)
    error_message: str | None = None
    try:
        await client.connect()
        await client.log_out()
    except Exception as exc:  # noqa: BLE001 - best-effort logout; surface the reason.
        error_message = str(exc)
    finally:
        await client.disconnect()
    if wipe_session:
        # Evict the pooled client first: on Windows it holds the ``.session``
        # SQLite file open, so unlinking under a live handle raises
        # PermissionError → the reset endpoint 500s.
        await evict_client(request.account_id)
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
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status=status,
        is_temporary=is_temporary,
        error_type=type(exc).__name__,
        error_message=str(exc),
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
