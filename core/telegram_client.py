from __future__ import annotations

import asyncio
import mimetypes
import random
from contextlib import asynccontextmanager, suppress
from io import BytesIO
from typing import TYPE_CHECKING

from anyio import Path
from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon import TelegramClient, errors, utils
from telethon.tl.functions.account import (
    SaveMusicRequest,
    UpdateProfileRequest,
    UpdateStatusRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.functions.stories import CanSendStoryRequest, SendStoryRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowCloseFriends,
    InputPrivacyValueAllowContacts,
    ReactionEmoji,
)

from core.config import settings
from core.db import fetch_account_proxy_settings
from core.device_fingerprint import get_or_create_device_fingerprint
from core.logging import log_event
from schemas.device_fingerprint import TelegramClientProfile, TelegramClientRequest
from schemas.spam_status import SpamStatusProbe
from schemas.telegram_actions import ActionResult, AddProfileMusic, PostStory, SetProfilePhoto
from schemas.telegram_session import (
    SessionCheckStatus,
    TelegramSessionCheckRequest,
    TelegramSessionCheckResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from telethon.tl.types import TypeInputMedia, TypeInputPrivacyRule

    from schemas.telegram_actions import (
        ActionStatus,
        ReactToPost,
        ReadChannel,
        SendDirectMessage,
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
            flood_sleep_threshold=settings.telegram.flood_sleep_threshold,
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
        flood_sleep_threshold=settings.telegram.flood_sleep_threshold,
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


async def _flood_action_result(
    account_id: str,
    action: TelegramAction,
    *,
    status: ActionStatus,
    seconds: int | None,
) -> ActionResult:
    """Log a Telegram rate-limit event and build the matching ``ActionResult``.

    Covers the differentiated flood family — generic flood-wait, per-peer
    ``PEER_FLOOD`` (no duration), per-chat slow mode, and premium-gated waits —
    so callers can react per type instead of treating a moderation restriction
    as an ordinary failure.
    """
    await log_event(
        "WARNING",
        f"telegram_{action.action_type}_{status}",
        account_id=account_id,
        extra={"seconds": seconds},
    )
    return ActionResult(
        status=status,
        action_type=action.action_type,
        account_id=account_id,
        flood_wait_seconds=seconds,
    )


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """Dispatch a typed Telegram action against ``account_id``.

    The only entry point for Telethon calls from outside ``core/``. Builds the
    account's client (with proxy + device fingerprint), runs the action,
    classifies the Telegram rate-limit family (flood-wait / slow-mode /
    premium / peer-flood) separately, logs every outcome, and returns a typed
    ``ActionResult`` — never raises Telethon errors upward.
    """
    request = TelegramClientRequest(account_id=account_id)
    async with telegram_client(request) as client:
        try:
            await client.connect()
            message_id = await _dispatch_action(client, action)
        except errors.SlowModeWaitError as exc:
            return await _flood_action_result(
                account_id,
                action,
                status="slow_mode_wait",
                seconds=exc.seconds,
            )
        except errors.FloodPremiumWaitError as exc:
            return await _flood_action_result(
                account_id,
                action,
                status="premium_wait",
                seconds=exc.seconds,
            )
        except errors.PeerFloodError:
            return await _flood_action_result(
                account_id,
                action,
                status="peer_flood",
                seconds=None,
            )
        except errors.FloodWaitError as exc:
            return await _flood_action_result(
                account_id,
                action,
                status="flood_wait",
                seconds=exc.seconds,
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


_SPAMBOT_USERNAME = "SpamBot"


async def check_spam_status(account_id: str) -> SpamStatusProbe:
    """Probe @SpamBot and read self-restriction flags for an account.

    Sends ``/start`` to @SpamBot and captures its reply, and reads the account's
    own ``restricted`` / ``restriction_reason`` flags via ``get_me``. The raw
    result is parsed and cached by ``services.spam_status`` — never raises.
    """
    request = TelegramClientRequest(account_id=account_id)
    async with telegram_client(request) as client:
        try:
            await client.connect()
            reply_text = await _probe_spambot(client)
            restricted, reason = await _probe_self_restriction(client)
        except Exception as exc:  # noqa: BLE001 - any probe failure classifies as unknown.
            await log_event(
                "WARNING",
                "telegram_spam_status_probe_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )
            return SpamStatusProbe(account_id=account_id, error=f"{type(exc).__name__}: {exc}")
    return SpamStatusProbe(
        account_id=account_id,
        reply_text=reply_text,
        restricted=restricted,
        restriction_reason=reason,
    )


async def _probe_spambot(client: TelegramClient) -> str | None:
    """Open a conversation with @SpamBot, send ``/start`` and return its reply."""
    async with client.conversation(
        _SPAMBOT_USERNAME,
        timeout=settings.telegram.timeout_seconds,
    ) as conv:
        await conv.send_message("/start")
        response = await conv.get_response()
        return _optional_str(getattr(response, "text", None))


async def _probe_self_restriction(client: TelegramClient) -> tuple[bool, str | None]:
    """Read the account's own ``restricted`` flag + reason (terms/country)."""
    me = await client.get_me()
    restricted = bool(getattr(me, "restricted", False))
    reasons = getattr(me, "restriction_reason", None) or []
    reason = "; ".join(
        str(getattr(item, "text", "") or getattr(item, "reason", "")) for item in reasons
    ).strip("; ")
    return restricted, (reason or None)


def _typing_seconds(text: str) -> float:
    """Length-proportional typing time (≈ WPM), clamped to a sane window."""
    warm = settings.warming
    base = len(text) * 60.0 / (5.0 * warm.typing_wpm)
    return max(warm.typing_sim_min_seconds, min(warm.typing_sim_max_seconds, base))


async def _send_dm_with_typing(client: TelegramClient, action: SendDirectMessage) -> int | None:
    """Send a DM, optionally preceded by a length-proportional "typing…" action."""
    if settings.warming.typing_simulation_enabled:
        async with client.action(action.user_id, "typing"):  # ty: ignore[invalid-context-manager]
            await asyncio.sleep(_typing_seconds(action.text))
            message = await client.send_message(action.user_id, action.text)
    else:
        message = await client.send_message(action.user_id, action.text)
    return int(getattr(message, "id", 0)) or None


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
        message_id = await _send_dm_with_typing(client, action)
    elif action.action_type in {"set_profile_photo", "post_story", "add_profile_music"}:
        message_id = await _dispatch_profile_media_action(client, action)
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


async def _dispatch_profile_media_action(
    client: TelegramClient,
    action: TelegramAction,
) -> int | None:
    if isinstance(action, SetProfilePhoto):
        await _set_profile_photo(client, action.filename, action.content)
        return None
    if isinstance(action, PostStory):
        return await _post_story(client, action)
    if isinstance(action, AddProfileMusic):
        await _add_profile_music(client, action)
        return None
    msg = f"Unsupported profile media action_type: {action.action_type}"
    raise ValueError(msg)


async def _set_profile_photo(client: TelegramClient, filename: str, content: bytes) -> None:
    uploaded = await client.upload_file(_named_bytes(filename, content), file_name=filename)
    await client(UploadProfilePhotoRequest(file=uploaded))


async def _post_story(client: TelegramClient, action: PostStory) -> int | None:
    peer = await client.get_input_entity("me")
    await client(CanSendStoryRequest(peer=peer))
    media = await _story_media(client, action.filename, action.content, action.media_kind)
    result = await client(
        SendStoryRequest(
            peer=peer,
            media=media,
            privacy_rules=_story_privacy_rules(action.privacy_preset),
            caption=action.caption or "",
            period=action.period_seconds,
            noforwards=action.protect_content,
        ),
    )
    story_id = getattr(result, "id", None)
    return story_id if isinstance(story_id, int) else None


async def _story_media(
    client: TelegramClient,
    filename: str,
    content: bytes,
    media_kind: str,
) -> TypeInputMedia:
    uploaded = await client.upload_file(_named_bytes(filename, content), file_name=filename)
    if media_kind == "image":
        return InputMediaUploadedPhoto(file=uploaded)
    attributes, mime_type = utils.get_attributes(
        _named_bytes(filename, content),
        mime_type=mimetypes.guess_type(filename)[0] or "video/mp4",
        supports_streaming=True,
    )
    return InputMediaUploadedDocument(
        file=uploaded,
        mime_type=mime_type,
        attributes=attributes,
    )


def _story_privacy_rules(preset: str) -> list[TypeInputPrivacyRule]:
    if preset == "public":
        return [InputPrivacyValueAllowAll()]
    if preset == "close_friends":
        return [InputPrivacyValueAllowCloseFriends()]
    return [InputPrivacyValueAllowContacts()]


async def _add_profile_music(client: TelegramClient, action: AddProfileMusic) -> None:
    attributes, mime_type = utils.get_attributes(
        _named_bytes(action.filename, action.content),
        attributes=[
            DocumentAttributeAudio(
                duration=0,
                title=action.title,
                performer=action.performer,
            ),
        ],
        mime_type=mimetypes.guess_type(action.filename)[0] or "audio/mpeg",
    )
    message = await client.send_file(
        "me",
        _named_bytes(action.filename, action.content),
        file_name=action.filename,
        attributes=attributes,
        mime_type=mime_type,
    )
    document = getattr(message, "document", None)
    if document is None:
        msg = "Telegram did not return an audio document"
        raise ValueError(msg)
    await client(SaveMusicRequest(id=utils.get_input_document(document)))
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int):
        with suppress(Exception):
            await client.delete_messages("me", [message_id], revoke=True)


def _named_bytes(filename: str, content: bytes) -> BytesIO:
    stream = BytesIO(content)
    stream.name = filename
    return stream


def _action_log_extra(action: TelegramAction) -> dict[str, object]:
    """Compact summary of an action for log extras — no payload secrets."""
    extra: dict[str, object]
    if action.action_type in {"join_channel", "leave_channel", "read_channel", "react_to_post"}:
        extra = {"channel": getattr(action, "channel", "")}
    elif action.action_type == "post_comment":
        extra = {"chat_id": getattr(action, "chat_id", 0)}
    elif action.action_type == "set_online":
        extra = {"online": getattr(action, "online", None)}
    elif action.action_type == "send_dm":
        extra = {"user_id": getattr(action, "user_id", 0)}
    elif action.action_type == "update_profile":
        extra = {
            "has_last_name": getattr(action, "last_name", None) is not None,
            "has_username": getattr(action, "username", None) is not None,
            "has_bio": getattr(action, "bio", None) is not None,
        }
    elif action.action_type in {"set_profile_photo", "post_story", "add_profile_music"}:
        extra = {"filename": getattr(action, "filename", "")}
    else:
        extra = {}
    return extra
