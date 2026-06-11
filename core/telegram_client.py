from __future__ import annotations

import random
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anyio import Path
from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon import TelegramClient, errors
from telethon.tl.functions.account import (
    UpdateProfileRequest,
    UpdateStatusRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

from core.config import settings
from core.db import fetch_account_proxy_settings
from core.device_fingerprint import get_or_create_device_fingerprint
from core.logging import log_event
from schemas.device_fingerprint import TelegramClientProfile, TelegramClientRequest
from schemas.telegram_actions import ActionResult
from schemas.telegram_session import (
    SessionCheckStatus,
    TelegramSessionCheckRequest,
    TelegramSessionCheckResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.telegram_actions import (
        ReactToPost,
        ReadChannel,
        TelegramAction,
        UpdateProfile,
    )


# SystemRandom: non-cryptographic jitter/selection, but avoids the module-level
# `random.*` calls that ruff S311 flags. Behaviour is identical for our needs.
_rng = random.SystemRandom()


def _session_path(request: TelegramClientRequest) -> str:
    session_name = request.session_name or request.account_id
    return str(settings.telegram.session_dir / session_name)


async def prepare_telegram_client_profile(
    request: TelegramClientRequest,
) -> TelegramClientProfile:
    await _ensure_session_dir()
    device = await get_or_create_device_fingerprint(request.account_id)
    proxy = await fetch_account_proxy_settings(request.account_id)
    return TelegramClientProfile(
        account_id=request.account_id,
        session_path=_session_path(request),
        receive_updates=request.receive_updates,
        device=device,
        proxy_type=proxy.proxy_type if proxy else None,
        proxy_host=proxy.host if proxy else None,
        proxy_port=proxy.port if proxy else None,
        proxy_username=proxy.username if proxy else None,
        proxy_password=proxy.password if proxy else None,
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
    await Path(settings.telegram.session_dir).mkdir(parents=True, exist_ok=True)


def create_telegram_client(profile: TelegramClientProfile) -> TelegramClient:
    device = profile.device
    proxy = _proxy_config(profile)
    if proxy is not None:
        return TelegramClient(
            profile.session_path,
            settings.telegram.api_id,
            settings.telegram.api_hash,
            device_model=device.device_model,
            system_version=device.system_version,
            app_version=device.app_version,
            lang_code=device.lang_code,
            system_lang_code=device.system_lang_code,
            receive_updates=profile.receive_updates,
            timeout=settings.telegram.timeout_seconds,
            connection_retries=settings.telegram.connection_retries,
            retry_delay=settings.telegram.retry_delay_seconds,
            request_retries=settings.telegram.request_retries,
            proxy=proxy,
        )
    return TelegramClient(
        profile.session_path,
        settings.telegram.api_id,
        settings.telegram.api_hash,
        device_model=device.device_model,
        system_version=device.system_version,
        app_version=device.app_version,
        lang_code=device.lang_code,
        system_lang_code=device.system_lang_code,
        receive_updates=profile.receive_updates,
        timeout=settings.telegram.timeout_seconds,
        connection_retries=settings.telegram.connection_retries,
        retry_delay=settings.telegram.retry_delay_seconds,
        request_retries=settings.telegram.request_retries,
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


def _proxy_config(profile: TelegramClientProfile) -> dict[str, object] | None:
    if not profile.proxy_type or not profile.proxy_host or profile.proxy_port is None:
        return None
    return {
        "proxy_type": profile.proxy_type,
        "addr": profile.proxy_host,
        "port": profile.proxy_port,
        "rdns": True,
        "username": profile.proxy_username,
        "password": profile.proxy_password,
    }


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """Dispatch a typed Telegram action against ``account_id``.

    The only entry point for Telethon calls from outside ``core/``. Builds the
    account's client (with proxy + device fingerprint), runs the action,
    catches ``FloodWaitError`` separately, logs every outcome, and returns a
    typed ``ActionResult`` — never raises Telethon errors upward.
    """
    request = TelegramClientRequest(account_id=account_id)
    async with telegram_client(request) as client:
        try:
            await client.connect()
            message_id = await _dispatch_action(client, action)
        except errors.FloodWaitError as exc:
            await log_event(
                "WARNING",
                f"telegram_{action.action_type}_flood_wait",
                account_id=account_id,
                extra={"seconds": exc.seconds},
            )
            return ActionResult(
                status="flood_wait",
                action_type=action.action_type,
                account_id=account_id,
                flood_wait_seconds=exc.seconds,
            )
        except Exception as exc:  # noqa: BLE001 — Telethon throws diverse errors; classify and report.
            await log_event(
                "ERROR",
                f"telegram_{action.action_type}_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )
            return ActionResult(
                status="failed",
                action_type=action.action_type,
                account_id=account_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    await log_event(
        "INFO",
        f"telegram_{action.action_type}",
        account_id=account_id,
        extra=_action_log_extra(action),
    )
    return ActionResult(
        status="ok",
        action_type=action.action_type,
        account_id=account_id,
        message_id=message_id,
    )


async def _dispatch_action(client: TelegramClient, action: TelegramAction) -> int | None:
    """Run one action against an already-connected client. Returns message_id if any.

    Single exit point keeps the branch count linting-friendly as the action set
    grows; the per-action body is delegated to small helpers where it is more
    than a one-liner.
    """
    # Telethon resolves usernames / chat refs at runtime; ty insists on the
    # narrow InputChannel union, so the str/int passthrough needs an ignore.
    message_id: int | None = None
    if action.action_type == "join_channel":
        await client(JoinChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
    elif action.action_type == "leave_channel":
        await client(LeaveChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
    elif action.action_type == "post_comment":
        message = await client.send_message(action.chat_id, action.text)
        message_id = int(getattr(message, "id", 0)) or None
    elif action.action_type == "update_profile":
        await _dispatch_update_profile(client, action)
    elif action.action_type == "set_online":
        await client(UpdateStatusRequest(offline=not action.online))
    elif action.action_type == "read_channel":
        await _dispatch_read_channel(client, action)
    elif action.action_type == "react_to_post":
        message_id = await _dispatch_react_to_post(client, action)
    elif action.action_type == "send_dm":
        message = await client.send_message(action.user_id, action.text)
        message_id = int(getattr(message, "id", 0)) or None
    else:
        msg = f"Unsupported action_type: {action.action_type}"
        raise ValueError(msg)
    return message_id


async def _dispatch_update_profile(client: TelegramClient, action: UpdateProfile) -> None:
    await client(
        UpdateProfileRequest(
            first_name=action.first_name,
            last_name=action.last_name or "",
            about=action.bio,
        ),
    )
    if action.username is not None:
        await client(UpdateUsernameRequest(username=action.username))


async def _dispatch_read_channel(client: TelegramClient, action: ReadChannel) -> None:
    """Fetch recent posts and mark them read — the "reading a feed" emulation."""
    messages = await client.get_messages(action.channel, limit=action.message_limit)
    # get_messages(limit=...) returns an iterable TotalList; the stub union also
    # admits a single Message / None for the by-id form, which we never use here.
    max_id = max(
        (int(getattr(message, "id", 0)) for message in messages),  # ty: ignore[not-iterable]
        default=0,
    )
    if max_id:
        await client.send_read_acknowledge(action.channel, max_id=max_id)


async def _dispatch_react_to_post(client: TelegramClient, action: ReactToPost) -> int | None:
    """React to a random recent post with a random candidate emoji."""
    messages = await client.get_messages(action.channel, limit=action.message_limit)
    candidates = [
        int(getattr(m, "id", 0))
        for m in messages  # ty: ignore[not-iterable]
        if getattr(m, "id", None)
    ]
    if not candidates:
        return None
    message_id = _rng.choice(candidates)
    emoji = _rng.choice(action.reactions)
    peer = await client.get_input_entity(action.channel)
    await client(
        SendReactionRequest(
            peer=peer,
            msg_id=message_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ),
    )
    return message_id


def _action_log_extra(action: TelegramAction) -> dict[str, object]:
    """Compact summary of an action for log extras — no payload secrets."""
    if action.action_type in {"join_channel", "leave_channel", "read_channel", "react_to_post"}:
        return {"channel": getattr(action, "channel", "")}
    if action.action_type == "post_comment":
        return {"chat_id": getattr(action, "chat_id", 0)}
    if action.action_type == "set_online":
        return {"online": getattr(action, "online", None)}
    if action.action_type == "send_dm":
        return {"user_id": getattr(action, "user_id", 0)}
    if action.action_type == "update_profile":
        return {
            "has_last_name": getattr(action, "last_name", None) is not None,
            "has_username": getattr(action, "username", None) is not None,
            "has_bio": getattr(action, "bio", None) is not None,
        }
    return {}
