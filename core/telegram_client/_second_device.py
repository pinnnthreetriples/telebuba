"""Sign an account's second device in as its own, separate Telegram authorization.

A NEW client — a new ``.session`` file and a freshly minted fingerprint of the other
device type — asks Telegram for a login code. Telegram delivers it to the account's
existing session as a 777000 service message; that session reads it, cut off at the
newest message id snapshotted BEFORE the request, so no older code can be replayed.
The 2FA password, when Telegram asks for one, is the one stored for the account.

Nothing is kept unless everything succeeds. The fingerprint and the session name are
written only after the sign-in is confirmed for the SAME Telegram user, in one
transaction; any failure removes the half-made ``.session`` (after logging the new
authorization out when it had already signed in), and the existing session is
never touched. Per-account pacing of code requests is the service's policy.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, NamedTuple

from anyio import Path as AsyncPath
from telethon import errors

from core.config import settings
from core.db import fetch_account
from core.device_fingerprint import generate_random_device_fingerprint
from core.device_fingerprint_android import generate_android_fingerprint
from core.repositories._accounts_twofa import fetch_account_twofa_password
from core.repositories.device_fingerprint import (
    SecondDeviceConflictError,
    forget_second_device,
    record_second_device,
)
from core.telegram_client._auth import (
    _auth_lock,
    _remove_session_file,
    log_out_session,
    remove_account_session,
)
from core.telegram_client._client import (
    DeviceUnavailableError,
    create_telegram_client,
    mobile_api_configured,
    prepare_telegram_client_profile,
)
from core.telegram_client._login_code import (
    DEFAULT_CODE_LENGTH,
    last_service_message_id,
    wait_for_login_code,
)
from core.telegram_client._pool import (
    TelegramClientPoolError,
    forget_primary_device,
    get_client,
    removing_client,
)
from schemas.device_fingerprint import TelegramClientRequest
from schemas.devices import SecondDeviceResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.devices import DeviceRefusalCode, SecondDeviceSignIn

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)


class _SignInRefusedError(Exception):
    def __init__(self, code: DeviceRefusalCode) -> None:
        super().__init__(code)
        self.code: DeviceRefusalCode = code


class _Identity(NamedTuple):
    phone: str
    user_id: int


_CODE_TIMEOUT: DeviceRefusalCode = "device_code_timeout"
_PRIMARY_UNAVAILABLE: DeviceRefusalCode = "device_primary_unavailable"
_TWOFA_MISSING: DeviceRefusalCode = "device_twofa_missing"
_USER_MISMATCH: DeviceRefusalCode = "device_user_mismatch"
# Telegram answers that end the attempt, each with the code the operator is shown.
_ERROR_CODES: tuple[
    tuple[type[Exception] | tuple[type[Exception], ...], DeviceRefusalCode], ...
] = (
    (
        (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError, errors.PhoneCodeEmptyError),
        "device_code_invalid",
    ),
    (errors.PasswordHashInvalidError, "device_twofa_invalid"),
    (errors.ConnectionLangPackInvalidError, "device_lang_pack_invalid"),
    (SecondDeviceConflictError, "device_already_present"),
)


def _refusal(
    request: SecondDeviceSignIn,
    code: DeviceRefusalCode,
    *,
    error_type: str | None = None,
    flood_wait_seconds: int | None = None,
) -> SecondDeviceResult:
    return SecondDeviceResult(
        account_id=request.account_id,
        device_type=request.device_type,
        code=code,
        error_type=error_type,
        flood_wait_seconds=flood_wait_seconds,
    )


async def _sign_in(
    client: TelegramClient,
    primary: TelegramClient,
    request: SecondDeviceSignIn,
    identity: _Identity,
) -> None:
    # Taken immediately before the request: any code already in 777000 is older.
    cutoff = await last_service_message_id(primary)
    sent = await client.send_code_request(identity.phone)
    code = await wait_for_login_code(
        primary,
        after_id=cutoff,
        wait_seconds=settings.telegram.second_device_code_wait_seconds,
        poll_seconds=settings.telegram.second_device_code_poll_seconds,
        length=int(getattr(getattr(sent, "type", None), "length", 0) or DEFAULT_CODE_LENGTH),
    )
    if code is None:
        raise _SignInRefusedError(_CODE_TIMEOUT)
    try:
        await client.sign_in(
            phone=identity.phone,
            code=code,
            phone_code_hash=sent.phone_code_hash,
        )
    except errors.SessionPasswordNeededError:
        password = await fetch_account_twofa_password(request.account_id)
        if not password:
            raise _SignInRefusedError(_TWOFA_MISSING) from None
        await client.sign_in(password=password)


def _classify(request: SecondDeviceSignIn, exc: Exception) -> SecondDeviceResult:
    """The refusal for a failed sign-in; the class name is the only diagnostic kept."""
    if isinstance(exc, _SignInRefusedError):
        return _refusal(request, exc.code)
    if isinstance(exc, errors.FloodWaitError):
        return _refusal(request, "device_flood_wait", flood_wait_seconds=exc.seconds)
    for error_types, code in _ERROR_CODES:
        if isinstance(exc, error_types):
            return _refusal(request, code)
    logger.warning("second-device sign-in failed for %s", request.account_id, exc_info=exc)
    return _refusal(request, "device_auth_failed", error_type=type(exc).__name__)


async def _confirm_same_user(client: TelegramClient, user_id: int) -> None:
    """Refuse to keep a sign-in that landed on a different Telegram user."""
    me = await client.get_me()
    if getattr(me, "id", None) != user_id:
        raise _SignInRefusedError(_USER_MISMATCH)


async def _discard(client: TelegramClient, session_path: str, *, signed_in: bool) -> None:
    """Undo a failed attempt: revoke a sign-in that happened, disconnect, remove the file.

    Each step runs whatever the one before did: a logout or disconnect that raises
    must not leave the half-made ``.session`` behind to refuse every retry.
    """
    steps = (("logout", client.log_out),) if signed_in else ()
    for step, call in (*steps, ("disconnect", client.disconnect)):
        try:
            await call()
        except Exception as exc:  # noqa: BLE001 - best effort; the file still goes below.
            logger.warning("second-device %s failed (%s)", step, type(exc).__name__)
    try:
        await _remove_session_file(session_path)
    except OSError as exc:
        logger.warning("second-device session file kept (%s)", type(exc).__name__)


async def _primary_identity(primary: TelegramClient) -> _Identity | None:
    """Who the existing session is; ``None`` when it is signed out.

    The phone and the user id come from the session itself, not the account row: an
    imported ``.session`` has neither stored until a session check has run.
    """
    me = await primary.get_me()
    if me is None:
        return None
    phone = getattr(me, "phone", None)
    return _Identity(f"+{phone}" if phone else "", int(getattr(me, "id", 0)))


async def _existing_session(  # noqa: PLR0911 - one refusal per precondition
    request: SecondDeviceSignIn,
) -> tuple[TelegramClient, _Identity] | SecondDeviceResult:
    """The account's existing session and who it is — or why there is nothing to read from."""
    if request.device_type == "mobile" and not mobile_api_configured():
        return _refusal(request, "mobile_api_not_configured")
    account = await fetch_account(request.account_id)
    if account is None:
        return _refusal(request, _PRIMARY_UNAVAILABLE)
    if request.device_type in account.devices:
        return _refusal(request, "device_already_present")
    try:
        primary = await get_client(request.account_id)
        identity = await _primary_identity(primary)
    except (TelegramClientPoolError, ConnectionError, TimeoutError, errors.RPCError) as exc:
        logger.warning("primary session unusable for %s", request.account_id, exc_info=exc)
        return _refusal(request, _PRIMARY_UNAVAILABLE, error_type=type(exc).__name__)
    if identity is None:
        return _refusal(request, _PRIMARY_UNAVAILABLE)
    if not identity.phone:
        return _refusal(request, "device_phone_unknown")
    return primary, identity


async def authorize_second_device(request: SecondDeviceSignIn) -> SecondDeviceResult:
    """Sign ``request.device_type`` in beside the account's existing device; see module doc."""
    existing = await _existing_session(request)
    if isinstance(existing, SecondDeviceResult):
        return existing
    primary, identity = existing
    if request.device_type == "mobile":
        fingerprint = generate_android_fingerprint(request.account_id, identity.phone)
    else:
        fingerprint = generate_random_device_fingerprint(request.account_id, phone=identity.phone)
    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(
            account_id=request.account_id,
            device_type=request.device_type,
            session_name=request.session_name,
            receive_updates=False,
        ),
        device=fingerprint,
    )
    # A file already at that name is an orphan or another account's credential:
    # never sign in over it, and never delete it on the way out.
    if await AsyncPath(f"{profile.session_path}.session").exists():
        return _refusal(request, "device_session_conflict")
    async with (
        _auth_lock(request.account_id),
        removing_client(request.account_id, request.device_type),
    ):
        refusal = await _attempt(
            request,
            create_telegram_client(profile),
            profile.session_path,
            primary,
            identity,
            fingerprint,
        )
    return refusal or SecondDeviceResult(
        account_id=request.account_id, device_type=request.device_type
    )


async def _attempt(  # noqa: PLR0913 - one sign-in's whole context
    request: SecondDeviceSignIn,
    client: TelegramClient,
    session_path: str,
    primary: TelegramClient,
    identity: _Identity,
    fingerprint: DeviceFingerprint,
) -> SecondDeviceResult | None:
    """One sign-in, recorded or undone; the refusal, or ``None`` once it is recorded."""
    signed_in = False
    recording: asyncio.Future[None] | None = None
    try:
        await client.connect()
        await _sign_in(client, primary, request, identity)
        signed_in = True
        await _confirm_same_user(client, identity.user_id)
        # The commit runs on a worker thread a cancellation cannot stop, so it is
        # shielded and its outcome read below rather than guessed.
        recording = asyncio.ensure_future(record_second_device(request.session_name, fingerprint))
        await asyncio.shield(recording)
    except asyncio.CancelledError:
        # Abandoned mid-sign-in: keep what was recorded, revoke and remove anything else.
        if await _recorded(recording):
            await asyncio.shield(_close(client, request.account_id))
        else:
            await asyncio.shield(_discard(client, session_path, signed_in=signed_in))
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is classified and undone.
        await _discard(client, session_path, signed_in=signed_in)
        return _classify(request, exc)
    await _close(client, request.account_id)
    return None


async def _recorded(recording: asyncio.Future[None] | None) -> bool:
    if recording is None:
        return False
    try:
        await recording
    except Exception:  # noqa: BLE001 - not recorded is the answer, whatever the reason.
        return False
    return True


async def _close(client: TelegramClient, account_id: str) -> None:
    """Disconnect a recorded sign-in; new borrows re-resolve the primary either way."""
    try:
        await client.disconnect()
    except Exception as exc:  # noqa: BLE001 - the device is recorded; the socket is moot.
        logger.warning("second-device disconnect failed (%s)", type(exc).__name__)
    finally:
        forget_primary_device(account_id)


async def remove_second_device(account_id: str) -> None:
    """Sign the account's second device out, delete its session and forget it.

    For a logout or a session reset of the account: a second sign-in left running
    after the operator ended the account's session would keep serving unseen, and a
    forgotten one can be added again. Best effort like every logout: a sign-out that
    never reaches Telegram still removes the file and the record.
    """
    account = await fetch_account(account_id)
    if account is None or account.second_session_name is None:
        return
    try:
        await log_out_session(
            TelegramClientRequest(
                account_id=account_id,
                session_name=account.second_session_name,
                receive_updates=False,
            ),
            wipe_session=True,
        )
    except DeviceUnavailableError:
        # Mobile is no longer configured, so no client can be built to sign it out.
        try:
            await remove_account_session(account_id, account.second_session_name)
        except OSError as exc:
            logger.warning("second-device session file kept (%s)", type(exc).__name__)
    await forget_second_device(account_id)
    forget_primary_device(account_id)
