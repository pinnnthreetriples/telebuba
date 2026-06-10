from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anyio import Path
from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon import TelegramClient, errors

from core.config import settings
from core.device_fingerprint import get_or_create_device_fingerprint
from schemas.device_fingerprint import TelegramClientProfile, TelegramClientRequest
from schemas.telegram_session import (
    SessionCheckStatus,
    TelegramSessionCheckRequest,
    TelegramSessionCheckResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _session_path(request: TelegramClientRequest) -> str:
    session_name = request.session_name or request.account_id
    return str(settings.session_dir / session_name)


async def prepare_telegram_client_profile(
    request: TelegramClientRequest,
) -> TelegramClientProfile:
    await _ensure_session_dir()
    device = await get_or_create_device_fingerprint(request.account_id)
    return TelegramClientProfile(
        account_id=request.account_id,
        session_path=_session_path(request),
        receive_updates=request.receive_updates,
        device=device,
    )


async def prepare_session_check_profile(
    request: TelegramSessionCheckRequest,
) -> TelegramClientProfile:
    return await prepare_telegram_client_profile(
        TelegramClientRequest(
            account_id=request.account_id,
            session_name=request.session_name,
            receive_updates=False,
        ),
    )


async def _ensure_session_dir() -> None:
    await Path(settings.session_dir).mkdir(parents=True, exist_ok=True)


def create_telegram_client(profile: TelegramClientProfile) -> TelegramClient:
    device = profile.device
    return TelegramClient(
        profile.session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        device_model=device.device_model,
        system_version=device.system_version,
        app_version=device.app_version,
        lang_code=device.lang_code,
        system_lang_code=device.system_lang_code,
        receive_updates=profile.receive_updates,
        timeout=settings.telegram_timeout_seconds,
        connection_retries=settings.telegram_connection_retries,
        retry_delay=settings.telegram_retry_delay_seconds,
        request_retries=settings.telegram_request_retries,
    )


@asynccontextmanager
async def telegram_client(request: TelegramClientRequest) -> AsyncIterator[TelegramClient]:
    profile = await prepare_telegram_client_profile(request)
    client = create_telegram_client(profile)
    try:
        yield client
    finally:
        await client.disconnect()


async def check_telegram_session(
    request: TelegramSessionCheckRequest,
) -> TelegramSessionCheckResult:
    profile = await prepare_session_check_profile(request)
    client = create_telegram_client(profile)
    result: TelegramSessionCheckResult
    try:
        await client.connect()
        if not await client.is_user_authorized():
            result = _status_session_check_result(profile, status="unauthorized")
        else:
            result = _alive_session_check_result(profile, await client.get_me())
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


def _alive_session_check_result(
    profile: TelegramClientProfile,
    me: object,
) -> TelegramSessionCheckResult:
    user_id = getattr(me, "id", None)
    return TelegramSessionCheckResult(
        account_id=profile.account_id,
        session_path=profile.session_path,
        status="alive",
        is_temporary=False,
        user_id=user_id if isinstance(user_id, int) else None,
        phone=_optional_str(getattr(me, "phone", None)),
        username=_optional_str(getattr(me, "username", None)),
        first_name=_optional_str(getattr(me, "first_name", None)),
        last_name=_optional_str(getattr(me, "last_name", None)),
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


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
