"""Typed-action executor — the only entry point for Telethon calls from outside core/."""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import (
    UpdateProfileRequest,
    UpdateStatusRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    JoinChannelRequest,
    LeaveChannelRequest,
)
from telethon.tl.functions.messages import ImportChatInviteRequest, SendReactionRequest
from telethon.tl.types import ReactionEmoji

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.telegram_client._action_results import (
    _flood_action_result,
    _generic_error,
    _unavailable_result,
)
from core.telegram_client._media import _dispatch_profile_media_action
from core.telegram_client._pool import TelegramClientPoolError, get_client
from core.telegram_client._react import _channel_reaction_whitelist, _pick_reaction
from core.telegram_client._read_stories import dispatch_watch_peer_stories
from core.telegram_client._util import extract_invite_hash
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    ClickButton,
    CommentOnPost,
    JoinChannel,
    JoinDiscussionGroup,
    LeaveChannel,
    PostComment,
    PostStory,
    ReactToPost,
    ReadChannel,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    RemoveStory,
    SendDirectMessage,
    SetMainProfilePhoto,
    SetOnline,
    SetProfilePhoto,
    ToggleStoryPinned,
    UpdateProfile,
    WatchPeerStories,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction

# SystemRandom: non-cryptographic jitter/selection, but avoids the module-level
# `random.*` calls that ruff S311 flags. Behaviour is identical for our needs.
_rng = random.SystemRandom()


@dataclass(frozen=True)
class _DispatchResult:
    """One action's dispatch outcome.

    Carries the ``message_id`` (if any) plus dynamic log fields the static
    ``_action_log_extra`` can't know — e.g. the reaction emoji the gateway
    actually placed, chosen at dispatch time.
    """

    message_id: int | None = None
    log_extra: dict[str, object] | None = None


async def execute(account_id: str, action: TelegramAction) -> ActionResult:  # noqa: C901, PLR0911 - one except per Telegram error family
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
        outcome = await _dispatch_action(client, action)
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
        if action.action_type in {"join_channel", "join_discussion_group"}:
            await log_event(
                "INFO",
                f"telegram_{action.action_type}_already_participant",
                account_id=account_id,
                extra={"channel": getattr(action, "channel", None)},
            )
            return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)
        return await _generic_error(account_id, action, exc)
    except (TelegramClientPoolError, ConnectionError, TimeoutError) as exc:
        return await _unavailable_result(account_id, action, exc)
    except Exception as exc:  # noqa: BLE001
        return await _generic_error(account_id, action, exc)

    extra = _action_log_extra(action)
    if outcome.log_extra:
        extra |= outcome.log_extra
    await log_event(
        "INFO",
        f"telegram_{action.action_type}",
        account_id=account_id,
        extra=extra,
    )
    return ActionResult(
        status="ok",
        action_type=action.action_type,
        account_id=account_id,
        message_id=outcome.message_id,
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


async def _dispatch_action(client: TelegramClient, action: TelegramAction) -> _DispatchResult:  # noqa: C901, PLR0912
    """Run one action against an already-connected client.

    Pattern-matches on the concrete action model so ty narrows ``action`` inside
    each branch; a single exit keeps the return count lint-friendly as the action
    set grows, and bodies are delegated to helpers where more than a one-liner.
    Returns the ``message_id`` (if any) and any dynamic log fields the action
    produced at dispatch time (e.g. the reaction emoji actually placed).
    """
    # Telethon resolves usernames / chat refs at runtime; ty insists on the
    # narrow InputChannel union, so the str/int passthrough needs an ignore.
    message_id: int | None = None
    log_extra: dict[str, object] | None = None
    match action:
        case JoinChannel():
            hash_str = extract_invite_hash(action.channel)
            if hash_str:
                await client(ImportChatInviteRequest(hash=hash_str))
            else:
                await client(JoinChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
        case JoinDiscussionGroup():
            await _dispatch_join_discussion_group(client, action)
        case LeaveChannel():
            await client(LeaveChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
        case PostComment():
            message = await client.send_message(action.chat_id, action.text)
            message_id = int(getattr(message, "id", 0)) or None
        case CommentOnPost():
            message = await client.send_message(
                action.channel,
                action.text,
                comment_to=action.post_id,
            )
            message_id = int(getattr(message, "id", 0)) or None
        case ClickButton():
            await _dispatch_click_button(client, action)
        case UpdateProfile():
            await _dispatch_update_profile(client, action)
        case SetOnline():
            await client(UpdateStatusRequest(offline=not action.online))
        case ReadChannel():
            await _dispatch_read_channel(client, action)
        case WatchPeerStories():
            log_extra = {"stories_seen": await dispatch_watch_peer_stories(client, action)}
        case ReactToPost():
            react = await _dispatch_react_to_post(client, action)
            message_id = react.message_id
            log_extra = react.log_extra
        case SendDirectMessage():
            message_id = await _send_dm_with_typing(client, action)
        case _:
            # Everything else is a profile-media write (photo / story / music);
            # its own dispatcher raises for anything genuinely unhandled.
            message_id = await _dispatch_profile_media_action(client, action)
    return _DispatchResult(message_id=message_id, log_extra=log_extra)


async def _dispatch_update_profile(client: TelegramClient, action: UpdateProfile) -> None:
    """Field contract: ``""`` clears, ``None`` leaves unchanged (omitted from TL flags).

    The username goes FIRST: it is the fallible call (occupied/invalid handle),
    and sending it after ``UpdateProfileRequest`` used to half-apply the edit —
    name/bio already changed on Telegram while the UI reported "nothing saved"
    and the DB snapshot stayed stale.
    """
    if action.username is not None:
        # Re-sending the account's current username is a no-op, not a failure.
        with suppress(errors.UsernameNotModifiedError):
            await client(UpdateUsernameRequest(username=action.username))
    await client(
        UpdateProfileRequest(
            first_name=action.first_name,
            last_name=action.last_name,
            about=action.bio,
        ),
    )


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


async def _dispatch_react_to_post(client: TelegramClient, action: ReactToPost) -> _DispatchResult:
    """React to a random recent post with an emoji the channel actually permits.

    Picking blindly from the configured set trips ``ReactionInvalidError`` on
    channels that restrict reactions (e.g. @durov). We first read the channel's
    allowed set and prefer one of our emoji from it; if none overlap we still
    react with one of the channel's own (non-negative) emoji so a reaction lands.
    The outcome always rides back in ``log_extra`` so the activity log can show
    it: the placed emoji on success, or a ``reaction_skip`` reason (no recent
    posts / no usable emoji) when nothing landed.
    """
    messages = await client.get_messages(action.channel, limit=action.message_limit)
    candidates = [
        int(getattr(m, "id", 0))
        for m in messages  # ty: ignore[not-iterable]
        if getattr(m, "id", None)
    ]
    if not candidates:
        return _DispatchResult(log_extra={"reaction_skip": "no_posts"})
    allowed = await _channel_reaction_whitelist(client, action.channel)
    emoji = _pick_reaction(action.reactions, allowed)
    if emoji is None:
        return _DispatchResult(log_extra={"reaction_skip": "no_emoji"})
    message_id = _rng.choice(candidates)
    peer = await client.get_input_entity(action.channel)
    await client(
        SendReactionRequest(
            peer=peer,
            msg_id=message_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ),
    )
    return _DispatchResult(message_id=message_id, log_extra={"reaction": emoji})


async def _dispatch_join_discussion_group(
    client: TelegramClient,
    action: JoinDiscussionGroup,
) -> None:
    """Resolve ``channel``'s linked discussion group and join it.

    ``GetFullChannelRequest`` returns a ``messages.ChatFull`` whose ``full_chat``
    carries ``linked_chat_id`` and whose ``chats`` list holds the resolved
    ``Channel`` entities (with ``access_hash``). We join that entity directly —
    the linked group has no username, so it can't be joined by handle. A
    ``None`` ``linked_chat_id`` (comments disabled) raises ``ValueError`` so the
    executor classifies it as a generic failure rather than silently no-op.
    """
    full = await client(GetFullChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
    linked = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
    if linked is None:
        msg = f"No linked discussion group for {action.channel!r}"
        raise ValueError(msg)
    linked_id = int(linked)
    entity = next(
        (chat for chat in getattr(full, "chats", []) if int(getattr(chat, "id", 0)) == linked_id),
        None,
    )
    if entity is None:
        msg = f"Linked group {linked_id} not in ChatFull.chats for {action.channel!r}"
        raise ValueError(msg)
    await client(JoinChannelRequest(channel=entity))


async def _dispatch_click_button(client: TelegramClient, action: ClickButton) -> None:
    """Click an inline button on a stored message; no-op if the message is gone.

    Index-first selector: ``button_index`` if set, else ``button_text``, else
    the first button. We don't surface the callback answer.
    """
    message = await client.get_messages(action.chat_id, ids=action.message_id)
    if not message:
        return
    if action.button_text is not None:
        await message.click(text=action.button_text)  # ty: ignore[unresolved-attribute]
    else:
        index = action.button_index if action.button_index is not None else 0
        await message.click(index)  # ty: ignore[unresolved-attribute]


def _action_log_extra(action: TelegramAction) -> dict[str, object]:  # noqa: C901, PLR0912
    """Compact summary of an action for log extras — no payload secrets."""
    extra: dict[str, object]
    match action:
        case JoinChannel() | JoinDiscussionGroup() | LeaveChannel() | ReadChannel() | ReactToPost():
            extra = {"channel": action.channel}
        case WatchPeerStories():
            extra = {"peer": action.peer}
        case PostComment():
            extra = {"chat_id": action.chat_id}
        case CommentOnPost():
            extra = {"channel": action.channel, "post_id": action.post_id}
        case ClickButton():
            extra = {"chat_id": action.chat_id, "message_id": action.message_id}
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
        case RemoveProfilePhoto() | SetMainProfilePhoto():
            extra = {"photo_id": action.photo_id}
        case RemoveStory():
            extra = {"story_id": action.story_id}
        case ToggleStoryPinned():
            extra = {"story_id": action.story_id, "pinned": action.pinned}
        case _:  # pragma: no cover - discriminated union is exhaustive
            extra = {}
    return extra
