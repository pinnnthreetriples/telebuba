"""Typed-action executor — the only entry point for Telethon calls from outside core/."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import (
    UpdateProfileRequest,
    UpdateStatusRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, SendReactionRequest
from telethon.tl.types import ReactionEmoji

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.telegram_client._media import _dispatch_profile_media_action
from core.telegram_client._pool import get_client
from core.telegram_client._util import extract_invite_hash
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    JoinChannel,
    LeaveChannel,
    PostComment,
    PostStory,
    ReactToPost,
    ReadChannel,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    SendDirectMessage,
    SetOnline,
    SetProfilePhoto,
    UpdateProfile,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import ActionStatus, TelegramAction

# SystemRandom: non-cryptographic jitter/selection, but avoids the module-level
# `random.*` calls that ruff S311 flags. Behaviour is identical for our needs.
_rng = random.SystemRandom()


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


async def _generic_error(account_id: str, action: TelegramAction, exc: Exception) -> ActionResult:
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


async def execute(account_id: str, action: TelegramAction) -> ActionResult:  # noqa: PLR0911
    """Dispatch a typed Telegram action against ``account_id``.

    The only entry point for Telethon calls from outside ``core/``. Borrows
    the per-account pooled client (first borrow pays the connect cost; every
    subsequent call reuses the open MTProto session), runs the action,
    classifies the Telegram rate-limit family (flood-wait / slow-mode /
    premium / peer-flood) separately, logs every outcome, and returns a typed
    ``ActionResult`` — never raises Telethon errors upward.
    """
    account = await fetch_account(account_id)
    if account is None:
        return ActionResult(
            status="failed",
            action_type=action.action_type,
            account_id=account_id,
            error_type="AccountNotFound",
            error_message="Account not found in database",
        )

    try:
        client = await get_client(account_id)
        message_id = await _dispatch_action(client, action)
    except errors.SlowModeWaitError as exc:
        return await _flood_action_result(
            account_id, action, status="slow_mode_wait", seconds=exc.seconds
        )
    except errors.FloodPremiumWaitError as exc:
        return await _flood_action_result(
            account_id, action, status="premium_wait", seconds=exc.seconds
        )
    except errors.PeerFloodError:
        return await _flood_action_result(account_id, action, status="peer_flood", seconds=None)
    except errors.FloodWaitError as exc:
        return await _flood_action_result(
            account_id, action, status="flood_wait", seconds=exc.seconds
        )
    except errors.UserAlreadyParticipantError as exc:
        if action.action_type == "join_channel":
            await log_event(
                "INFO",
                "telegram_join_channel_already_participant",
                account_id=account_id,
                extra={"channel": getattr(action, "channel", None)},
            )
            return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)
        return await _generic_error(account_id, action, exc)
    except Exception as exc:  # noqa: BLE001
        return await _generic_error(account_id, action, exc)

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


async def _dispatch_action(client: TelegramClient, action: TelegramAction) -> int | None:  # noqa: C901
    """Run one action against an already-connected client. Returns message_id if any.

    Pattern-matches on the concrete action model so ty narrows ``action`` inside
    each branch; a single exit keeps the return count lint-friendly as the action
    set grows, and bodies are delegated to helpers where more than a one-liner.
    """
    # Telethon resolves usernames / chat refs at runtime; ty insists on the
    # narrow InputChannel union, so the str/int passthrough needs an ignore.
    message_id: int | None = None
    match action:
        case JoinChannel():
            hash_str = extract_invite_hash(action.channel)
            if hash_str:
                await client(ImportChatInviteRequest(hash=hash_str))
            else:
                await client(JoinChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
        case LeaveChannel():
            await client(LeaveChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
        case PostComment():
            message = await client.send_message(action.chat_id, action.text)
            message_id = int(getattr(message, "id", 0)) or None
        case UpdateProfile():
            await _dispatch_update_profile(client, action)
        case SetOnline():
            await client(UpdateStatusRequest(offline=not action.online))
        case ReadChannel():
            await _dispatch_read_channel(client, action)
        case ReactToPost():
            message_id = await _dispatch_react_to_post(client, action)
        case SendDirectMessage():
            message_id = await _send_dm_with_typing(client, action)
        case (
            SetProfilePhoto()
            | PostStory()
            | AddProfileMusic()
            | RemoveProfileMusic()
            | RemoveProfilePhoto()
        ):
            message_id = await _dispatch_profile_media_action(client, action)
        case _:  # pragma: no cover - discriminated union is exhaustive
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
    extra: dict[str, object]
    match action:
        case JoinChannel() | LeaveChannel() | ReadChannel() | ReactToPost():
            extra = {"channel": action.channel}
        case PostComment():
            extra = {"chat_id": action.chat_id}
        case SetOnline():
            extra = {"online": action.online}
        case SendDirectMessage():
            extra = {"user_id": action.user_id}
        case UpdateProfile():
            extra = {
                "has_last_name": action.last_name is not None,
                "has_username": action.username is not None,
                "has_bio": action.bio is not None,
            }
        case SetProfilePhoto() | PostStory() | AddProfileMusic():
            extra = {"filename": action.filename}
        case RemoveProfileMusic():
            extra = {"file_id": action.file_id}
        case RemoveProfilePhoto():
            extra = {"photo_id": action.photo_id}
        case _:  # pragma: no cover - discriminated union is exhaustive
            extra = {}
    return extra
