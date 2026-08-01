"""Typed-action executor — the only entry point for Telethon calls from outside core/."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.channels import LeaveChannelRequest

from core.db import fetch_account
from core.logging import log_event
from core.telegram_client._action_results import (
    _applied_privacy_keys,
    _DispatchResult,
    _flood_action_result,
    _generic_error,
    _join_by_request_result,
    _unavailable_result,
)
from core.telegram_client._channels import _channel_log_extra, _dispatch_channel_action
from core.telegram_client._dm import _resolve_dm_peer, _send_dm_with_typing
from core.telegram_client._groups import (
    dispatch_join_channel,
    dispatch_join_discussion_group,
    dispatch_leave_discussion_group,
)
from core.telegram_client._media import ProfileGatewayError, _dispatch_profile_media_action
from core.telegram_client._pool import TelegramClientPoolError, get_client
from core.telegram_client._privacy import dispatch_set_privacy_settings
from core.telegram_client._profile import (
    _DEAD_SESSION_ERRORS,
    _PROFILE_EDIT_ACTION_TYPES,
    _dead_session_code,
    _dispatch_update_profile,
    _mark_account_status,
)
from core.telegram_client._react import dispatch_react_to_post
from core.telegram_client._read_stories import dispatch_watch_peer_stories
from core.telegram_client._util import event_name
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    ClickButton,
    CommentOnPost,
    JoinChannel,
    JoinDiscussionGroup,
    LeaveChannel,
    LeaveDiscussionGroup,
    MarkDirectMessageRead,
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
    SetPrivacySettings,
    SetProfilePhoto,
    ToggleStoryPinned,
    UpdateProfile,
    WatchPeerStories,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction


async def execute(  # noqa: C901, PLR0911, PLR0912 - one except per Telegram error family
    account_id: str,
    action: TelegramAction,
    *,
    domain: str | None = None,
) -> ActionResult:
    """Dispatch a typed Telegram action against ``account_id``.

    The only entry point for Telethon calls from outside ``core/``. Borrows
    the per-account pooled client (first borrow pays the connect cost; every
    subsequent call reuses the open MTProto session), runs the action,
    classifies the Telegram rate-limit family (flood-wait / slow-mode /
    premium / peer-flood) separately, logs every outcome, and returns a typed
    ``ActionResult`` — never raises Telethon errors upward.

    ``domain`` prefixes every event name this call writes
    (``warming_telegram_join_channel`` instead of ``telegram_join_channel``) so
    the calling domain's log feed sees its own gateway rows and no one else's —
    see :func:`core.telegram_client._util.event_name`. It is bound once per
    domain at ``services.<domain>._seams.execute``, so call sites pass nothing.
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
            account_id, action, status="slow_mode_wait", seconds=exc.seconds, domain=domain
        )
    except errors.FloodPremiumWaitError as exc:
        return await _flood_action_result(
            account_id, action, status="premium_wait", seconds=exc.seconds, domain=domain
        )
    except errors.PeerFloodError:
        return await _flood_action_result(
            account_id, action, status="peer_flood", seconds=None, domain=domain
        )
    # Frozen errors subclass FloodError (420) / BadRequestError (400); classify
    # them above FloodWaitError so the broader flood clause cannot swallow them
    # (mirrors check_telegram_session). The status write keeps the accounts list
    # honest without waiting for the next manual session check; frozen is a
    # permanent state, so it is recorded for EVERY action family.
    except (errors.FrozenMethodInvalidError, errors.FrozenParticipantMissingError) as exc:
        await _mark_account_status(account_id, "frozen")
        frozen = ProfileGatewayError("account_frozen")
        frozen.__cause__ = exc  # same chain ``raise ... from exc`` would build
        return await _generic_error(account_id, action, frozen, domain=domain)
    # A dead auth key / revoked session / deactivated account: the classification
    # ``check_telegram_session`` already makes, reused here so the operator is told
    # to re-login instead of reading the opaque ``failed``. Disjoint from the flood
    # family above (all ``UnauthorizedError``, never ``FloodError``), so the order
    # relative to those clauses does not matter. The DB status is deliberately not
    # written: unlike ``frozen`` this is what the session check itself records, and
    # a sticky write from any action family (warming included) would block
    # start_warming — the same reason ``_PROFILE_EDIT_ACTION_TYPES`` exists.
    except _DEAD_SESSION_ERRORS as exc:
        dead = ProfileGatewayError(_dead_session_code(exc))
        dead.__cause__ = exc  # same chain ``raise ... from exc`` would build
        return await _generic_error(account_id, action, dead, domain=domain)
    except errors.FloodWaitError as exc:
        if action.action_type in _PROFILE_EDIT_ACTION_TYPES:
            await _mark_account_status(account_id, "flood_wait")
        return await _flood_action_result(
            account_id,
            action,
            status="flood_wait",
            seconds=exc.seconds,
            # The one flood arm a partial privacy write can reach: setPrivacy is
            # per-key, so key 2 can flood after key 1 already applied. Slow mode,
            # premium waits and peer-flood belong to message-sending families that
            # issue a single call, so they have nothing partial to report.
            applied_privacy_keys=_applied_privacy_keys(exc),
            domain=domain,
        )
    except errors.UserAlreadyParticipantError as exc:
        if action.action_type in {"join_channel", "join_discussion_group"}:
            await log_event(
                "INFO",
                event_name(domain, f"telegram_{action.action_type}_already_participant"),
                account_id=account_id,
                extra={"channel": getattr(action, "channel", None)},
            )
            return ActionResult(
                status="already_participant",
                action_type=action.action_type,
                account_id=account_id,
            )
        return await _generic_error(account_id, action, exc, domain=domain)
    except errors.InviteRequestSentError as exc:
        return await _join_by_request_result(account_id, action, exc, domain=domain)
    except (TelegramClientPoolError, ConnectionError, TimeoutError) as exc:
        return await _unavailable_result(account_id, action, exc, domain=domain)
    except Exception as exc:  # noqa: BLE001
        return await _generic_error(account_id, action, exc, domain=domain)

    extra = _action_log_extra(action)
    if outcome.log_extra:
        extra |= outcome.log_extra
    await log_event(
        "INFO",
        event_name(domain, f"telegram_{action.action_type}"),
        account_id=account_id,
        extra=extra,
    )
    return ActionResult(
        status="ok",
        action_type=action.action_type,
        account_id=account_id,
        message_id=outcome.message_id,
        # int64 → decimal string at the JSON boundary (see ActionResult).
        channel_id=str(outcome.channel_id) if outcome.channel_id is not None else None,
        recent_message_ids=(
            [str(i) for i in outcome.recent_message_ids]
            if outcome.recent_message_ids is not None
            else None
        ),
    )


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
            await dispatch_join_channel(client, action)
        case JoinDiscussionGroup():
            await dispatch_join_discussion_group(client, action)
        case LeaveChannel():
            await client(LeaveChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
        case LeaveDiscussionGroup():
            return await dispatch_leave_discussion_group(client, action)
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
        case SetPrivacySettings():
            await dispatch_set_privacy_settings(client, action)
        case SetOnline():
            await client(UpdateStatusRequest(offline=not action.online))
        case ReadChannel():
            return await _dispatch_read_channel(client, action)
        case WatchPeerStories():
            log_extra = {"stories_seen": await dispatch_watch_peer_stories(client, action)}
        case ReactToPost():
            return await dispatch_react_to_post(client, action)
        case SendDirectMessage():
            message_id = await _send_dm_with_typing(client, action)
        case MarkDirectMessageRead():
            # send_read_acknowledge on a user peer marks the DM conversation read.
            await client.send_read_acknowledge(await _resolve_dm_peer(client, action))
        case _ if action.action_type.startswith("channel_"):
            # Channel management (create/edit/post/delete) — its own dispatcher
            # builds the full result (channel_create carries the new id).
            return await _dispatch_channel_action(client, action)
        case _:
            # Everything else is a profile-media write (photo / story / music);
            # its own dispatcher raises for anything genuinely unhandled.
            message_id = await _dispatch_profile_media_action(client, action)
    return _DispatchResult(message_id=message_id, log_extra=log_extra)


async def _dispatch_read_channel(client: TelegramClient, action: ReadChannel) -> _DispatchResult:
    """Fetch recent posts and mark them read — the "reading a feed" emulation.

    Returns the ids fetched so a following react on the same channel reuses them
    instead of issuing a second identical ``get_messages``.
    """
    messages = await client.get_messages(action.channel, limit=action.message_limit)
    # get_messages(limit=...) returns an iterable TotalList; the stub union also
    # admits a single Message / None for the by-id form, which we never use here.
    ids = [
        int(getattr(message, "id", 0))
        for message in messages  # ty: ignore[not-iterable]
        if getattr(message, "id", None)
    ]
    max_id = max(ids, default=0)
    if max_id:
        await client.send_read_acknowledge(action.channel, max_id=max_id)
    return _DispatchResult(recent_message_ids=ids)


async def _dispatch_click_button(client: TelegramClient, action: ClickButton) -> None:
    """Click an inline button on a stored message; no-op if the message is gone.

    Text-first selector: ``button_text`` if set, else ``button_index``, else
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
        case (
            JoinChannel()
            | JoinDiscussionGroup()
            | LeaveChannel()
            | LeaveDiscussionGroup()
            | ReadChannel()
            | ReactToPost()
        ):
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
        case SendDirectMessage() | MarkDirectMessageRead():
            extra = {"user_id": action.user_id}
        case UpdateProfile():
            extra = {
                "has_last_name": action.last_name is not None,
                "has_username": action.username is not None,
                "has_bio": action.bio is not None,
            }
        case SetPrivacySettings():
            # The LEVELS, not just which keys were touched: this action has a
            # fleet-wide, non-undoable caller (setPrivacy replaces a key's whole
            # rule vector), so the activity log is the only record of what was
            # pushed to N accounts. Unlike profile text, a privacy level is not
            # account content, so logging the value leaks nothing.
            extra = {
                "profile_photo": action.profile_photo,
                "bio": action.bio,
                "last_seen": action.last_seen,
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
        case _ if action.action_type.startswith("channel_"):
            extra = _channel_log_extra(action)
        case _:  # pragma: no cover - discriminated union is exhaustive
            extra = {}
    return extra
