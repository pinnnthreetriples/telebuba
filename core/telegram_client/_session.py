"""Session liveness check — classifies Telethon connect/auth outcomes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon import errors
from telethon.tl.functions.help import GetAppConfigRequest

from core.config import settings
from core.telegram_client._client import prepare_session_check_profile
from core.telegram_client._pool import (
    TelegramClientPoolError,
    TelegramClientUnavailableError,
    get_client,
)
from core.telegram_client._util import optional_str
from schemas.telegram_session import TelegramSessionCheckResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.device_fingerprint import TelegramClientProfile
    from schemas.telegram_session import SessionCheckStatus, TelegramSessionCheckRequest


async def check_telegram_session(
    request: TelegramSessionCheckRequest,
) -> TelegramSessionCheckResult:
    profile = await prepare_session_check_profile(request)
    if settings.telegram.api_id == 0 or not settings.telegram.api_hash:
        return TelegramSessionCheckResult(
            account_id=profile.account_id,
            session_path=profile.session_path,
            status="session_error",
            is_temporary=False,
            error_type="MissingCredentials",
            error_message=(
                "TELEGRAM__API_ID / TELEGRAM__API_HASH are not set in .env — "
                "fill them in to enable session checks."
            ),
        )
    result: TelegramSessionCheckResult
    try:
        # One deadline over the whole probe — see ``session_check_timeout_seconds``.
        # Nothing below bounds itself: the borrow queues on the pool's per-account
        # connect lock with no timeout, and every RPC after it (``is_user_authorized``,
        # ``get_me``, the freeze probe, the avatar) rides Telethon's ``users._call``,
        # which awaits the response future forever. Expiry raises ``TimeoutError``,
        # which ``_NETWORK_ERRORS`` already classifies as a temporary transport fault —
        # the same verdict the pool's own refusal gets, for the same reason: nothing
        # was learned about this session, so the row must not be marked broken.
        async with asyncio.timeout(settings.telegram.session_check_timeout_seconds):
            client = await _probe_client(profile.account_id)
            if not await client.is_user_authorized():
                result = _status_session_check_result(profile, status="unauthorized")
            else:
                me = await client.get_me()
                # A frozen account keeps an authorized session and get_me() succeeds,
                # so probe the app config for a freeze signal before declaring alive.
                result = await _frozen_session_check_result(
                    client, profile
                ) or _alive_session_check_result(
                    profile, me, await _download_avatar_thumb(client, me)
                )
    except _SESSION_ERRORS as exc:
        result = _error_session_check_result(profile, exc, status="session_error")
    except _ACCOUNT_ERRORS as exc:
        result = _error_session_check_result(profile, exc, status="account_error")
    # Frozen errors subclass FloodError (420) / BadRequestError (400); classify
    # them above FloodWaitError so the broader flood clause cannot swallow them.
    except (errors.FrozenMethodInvalidError, errors.FrozenParticipantMissingError) as exc:
        result = _error_session_check_result(profile, exc, status="frozen")
    except errors.FloodWaitError as exc:
        result = _error_session_check_result(
            profile,
            exc,
            status="flood_wait",
            is_temporary=True,
            flood_wait_seconds=exc.seconds,
        )
    except _PROXY_ERRORS as exc:
        result = _error_session_check_result(profile, exc, status="proxy_error", is_temporary=True)
    except _NETWORK_ERRORS as exc:
        result = _error_session_check_result(
            profile,
            exc,
            status="network_error",
            is_temporary=True,
        )
    except Exception as exc:  # noqa: BLE001 - session checks must classify unexpected SDK failures.
        result = _error_session_check_result(
            profile,
            exc,
            status="unknown_error",
            is_temporary=True,
        )
    return result


async def _probe_client(account_id: str) -> TelegramClient:
    """Borrow the account's pooled client, surfacing the pool's underlying cause.

    The check used to build its own throwaway client. Telethon keeps the
    account's ``.session`` SQLite file open with an uncommitted write
    transaction for as long as a client is connected, so a second client on the
    same file raised ``sqlite3.OperationalError: database is locked`` — an
    unmapped 500 on the Accounts screen for any account something else was
    holding (the neurocomment listener holds one for hours). The pool is the
    single owner of a session, so borrow from it instead of opening a rival
    connection; that also makes the verdict describe the connection the system
    actually works through, and skips the ~7 s handshake.

    ``TelegramClientPoolError`` only wraps a connect failure, so re-raising
    ``cause`` hands the real Telethon/proxy/network error to the caller's
    classification ladder rather than collapsing it to ``unknown_error``.
    :class:`TelegramClientUnavailableError` is the exception: the pool refused
    without connecting, so its ``cause`` is the pool's own bare ``RuntimeError``,
    which matched no arm in that ladder and landed in the catch-all. It travels
    unwrapped so the caller's dedicated arm sees it.

    The pool keys on ``account_id`` and passes no session name, so the request's
    ``session_name`` no longer selects the file directly — but it still decides
    it, because ``_session_path`` resolves the account row's stored name. The
    check therefore reports on exactly the file every other action opens.
    """
    try:
        return await get_client(account_id)
    except TelegramClientUnavailableError:
        raise
    except TelegramClientPoolError as exc:
        raise exc.cause from exc


_SESSION_ERRORS = (
    errors.AuthKeyDuplicatedError,
    errors.AuthKeyError,
    errors.AuthKeyInvalidError,
    errors.AuthKeyNotFound,
    errors.AuthKeyPermEmptyError,
    errors.AuthKeyUnregisteredError,
    errors.SessionExpiredError,
    errors.SessionRevokedError,
)
_ACCOUNT_ERRORS = (
    errors.InputUserDeactivatedError,
    errors.UserDeactivatedBanError,
    errors.UserDeactivatedError,
)
# ``TelegramClientUnavailableError`` rides with the transport family, exactly as
# ``_actions`` and ``_read`` group the pool's failures with ConnectionError /
# TimeoutError. It means the pool refused to serve a client at all — shutting down,
# or a login / logout / removal holds the tombstone — so nothing was learned about
# this session and the verdict must not read as a fault of the account. It used to
# reach the catch-all as a bare ``RuntimeError`` and answer ``unknown_error``, which
# ``services.accounts.sessions`` PERSISTS: pressing Check while a login was in
# flight left a healthy row looking broken until the next check. ``TimeoutError`` is
# load-bearing for the same reason: it is what the probe's own deadline above raises.
_NETWORK_ERRORS = (ConnectionError, OSError, TimeoutError, TelegramClientUnavailableError)
_PROXY_ERRORS = (ProxyConnectionError, ProxyError, ProxyTimeoutError)


def _status_session_check_result(
    profile: TelegramClientProfile,
    *,
    status: SessionCheckStatus,
    is_temporary: bool = False,
) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status=status,
        is_temporary=is_temporary,
    )


async def _download_avatar_thumb(client: object, me: object) -> bytes | None:
    """Best-effort small-size avatar download for the accounts-list thumbnail.

    ``download_big=False`` fetches the ~160px photo (compact JPEG, crisp at the
    32px list size). Never fails the check: any refusal (FloodWait, no photo,
    RPC) degrades to ``None`` and the row falls back to initials.
    """
    try:
        data = await client.download_profile_photo(me, file=bytes, download_big=False)  # ty: ignore[unresolved-attribute]
    except Exception:  # noqa: BLE001 - avatar is cosmetic; the check must still classify.
        return None
    if isinstance(data, (bytes, bytearray)) and data:
        return bytes(data)
    return None


async def _frozen_session_check_result(
    client: object,
    profile: TelegramClientProfile,
) -> TelegramSessionCheckResult | None:
    """Best-effort freeze probe via ``help.getAppConfig`` (callable while frozen).

    Returns a ``frozen`` result when the config carries a non-zero
    ``freeze_since_date``, else ``None`` so the caller declares the account alive.
    Any unexpected failure (network/RPC other than the freeze signal) degrades to
    ``None`` — mirrors ``_download_avatar_thumb``; a getAppConfig hiccup must never
    break a healthy check.
    """
    try:
        config = await client(GetAppConfigRequest(hash=0))  # ty: ignore[call-non-callable]
        fields = {entry.key: getattr(entry.value, "value", None) for entry in config.config.value}
    except Exception:  # noqa: BLE001 - the probe is best-effort; the check must still classify.
        return None
    freeze_since = fields.get("freeze_since_date")
    if not freeze_since:
        return None
    until = fields.get("freeze_until_date")
    appeal = fields.get("freeze_appeal_url")
    message = "Account is frozen by Telegram."
    if until:
        message += f" Frozen until unixtime {int(until)}."
    if appeal:
        message += f" Appeal: {appeal}"
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status="frozen",
        is_temporary=False,
        error_type="AccountFrozen",
        error_message=message,
    )


def _alive_session_check_result(
    profile: TelegramClientProfile,
    me: object,
    avatar_thumb: bytes | None,
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
        avatar_thumb=avatar_thumb,
    )


def _error_session_check_result(
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
