"""Session liveness check — classifies Telethon connect/auth outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon import errors

from core.config import settings
from core.telegram_client._client import create_telegram_client, prepare_session_check_profile
from core.telegram_client._util import optional_str
from schemas.telegram_session import TelegramSessionCheckResult

if TYPE_CHECKING:
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
    client = create_telegram_client(profile)
    result: TelegramSessionCheckResult
    try:
        await client.connect()
        if not await client.is_user_authorized():
            result = _status_session_check_result(profile, status="unauthorized")
        else:
            me = await client.get_me()
            result = _alive_session_check_result(
                profile, me, await _download_avatar_thumb(client, me)
            )
    except _SESSION_ERRORS as exc:
        result = _error_session_check_result(profile, exc, status="session_error")
    except _ACCOUNT_ERRORS as exc:
        result = _error_session_check_result(profile, exc, status="account_error")
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
    finally:
        await client.disconnect()
    return result


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
_NETWORK_ERRORS = (ConnectionError, OSError, TimeoutError)
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
