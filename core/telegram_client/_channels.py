"""Channel-management actions — create/edit/delete own channels + their posts.

Write-side dispatcher for the ``channel_*`` action family, mirroring
``_media.py``. Refusals surface as :class:`ChannelGatewayError` whose
``str(exc)`` is a stable, locale-neutral code (non-negotiable #12) — it rides
``execute``'s generic-exception ladder into ``ActionResult.error_message``
verbatim (same contract as ``StoryVideoNormalisationError``), and the SPA
translates it.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.channels import (
    CheckUsernameRequest,
    CreateChannelRequest,
    DeleteChannelRequest,
    EditPhotoRequest,
    EditTitleRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.messages import EditChatAboutRequest
from telethon.tl.types import (
    DocumentAttributeVideo,
    InputChannelEmpty,
    InputChatUploadedPhoto,
    PeerChannel,
)

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._io import _named_bytes
from core.telegram_client._video import normalize_channel_video_for_telegram
from schemas.telegram_actions import (
    CreateChannel,
    DeleteChannel,
    DeleteChannelPost,
    EditChannel,
    EditChannelPost,
    PublishChannelPost,
    SetChannelPhoto,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction

# Telethon refusal family → stable, locale-neutral code. Flood-family errors
# are deliberately NOT mapped — they must reach ``execute``'s dedicated
# flood-wait ladder unchanged.
_TELETHON_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (errors.UsernameInvalidError, "channel_username_invalid"),
    (errors.UsernameOccupiedError, "channel_username_occupied"),
    (errors.UsernamePurchaseAvailableError, "channel_username_occupied"),
    (errors.ChannelsTooMuchError, "channels_too_much"),
    (errors.ChannelsAdminPublicTooMuchError, "channels_admin_public_too_much"),
    (errors.UserRestrictedError, "user_restricted"),
    (errors.ChatAdminRequiredError, "chat_admin_required"),
    (errors.ChannelPrivateError, "channel_not_found"),
    (errors.ChannelInvalidError, "channel_not_found"),
)


class ChannelGatewayError(ValueError):
    """A channel action was refused; ``str(exc)`` is the stable code.

    ``channel_id`` is set when the refusal happened AFTER a channel was
    created (``channel_create``'s post-create username assignment): the
    channel exists as private, so the id rides along and the executor
    threads it into the failed ``ActionResult`` — the caller can adopt the
    channel instead of re-creating a duplicate. Nothing is ever rolled back
    (never auto-delete — repo data-safety rule).
    """

    def __init__(self, code: str, *, channel_id: int | None = None) -> None:
        self.code = code
        self.channel_id = channel_id
        super().__init__(code)


async def _dispatch_channel_action(
    client: TelegramClient,
    action: TelegramAction,
) -> _DispatchResult:
    try:
        return await _run_channel_action(client, action)
    except errors.RPCError as exc:
        mapped = _map_telethon_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _map_telethon_error(exc: errors.RPCError) -> ChannelGatewayError | None:
    for error_cls, code in _TELETHON_ERROR_CODES:
        if isinstance(exc, error_cls):
            return ChannelGatewayError(code)
    return None


async def _run_channel_action(
    client: TelegramClient,
    action: TelegramAction,
) -> _DispatchResult:
    match action:
        case CreateChannel():
            return await _create_channel(client, action)
        case EditChannel():
            await _edit_channel(client, action)
        case SetChannelPhoto():
            await _set_channel_photo(client, action)
        case DeleteChannel():
            entity = await _input_channel(client, action.channel_id)
            await client(DeleteChannelRequest(channel=entity))  # ty: ignore[invalid-argument-type]
        case PublishChannelPost():
            return await _publish_post(client, action)
        case EditChannelPost():
            await _edit_post(client, action)
        case DeleteChannelPost():
            entity = await _input_channel(client, action.channel_id)
            await client.delete_messages(entity, [action.post_id])  # ty: ignore[invalid-argument-type]
        case _:  # pragma: no cover - caller only routes channel actions here
            msg = f"Unsupported channel action_type: {action.action_type}"
            raise ValueError(msg)
    return _DispatchResult()


# int64 ceiling — a larger id cannot exist on Telegram, and the session-cache
# lookup would raise OverflowError (sqlite binding), not ValueError.
_TELEGRAM_ID_MAX = 2**63 - 1


async def _input_channel(client: TelegramClient, channel_id: int) -> object:
    """Resolve an owned channel's input entity from the session cache by id."""
    if not 0 < channel_id <= _TELEGRAM_ID_MAX:
        code = "channel_not_found"
        raise ChannelGatewayError(code)
    try:
        return await client.get_input_entity(PeerChannel(channel_id))
    except (ValueError, OverflowError) as exc:
        code = "channel_not_found"
        raise ChannelGatewayError(code) from exc


async def _create_channel(client: TelegramClient, action: CreateChannel) -> _DispatchResult:
    """Create a broadcast channel; pre-check the handle BEFORE creating.

    ``CheckUsernameRequest`` with ``InputChannelEmpty`` probes global
    availability without touching anything, so the deterministic occupied
    case fails before anything exists. A plain ``False`` answer (no RPC
    error) is an occupied handle.

    The pre-check cannot cover everything: ``UpdateUsernameRequest`` can
    still fail AFTER the create (e.g. ``CHANNELS_ADMIN_PUBLIC_TOO_MUCH`` is
    only raised by the assignment, not the probe). The channel then exists
    as private — the refusal carries its id (see
    :class:`ChannelGatewayError`) and nothing is rolled back.
    """
    if action.username is not None:
        available = await client(
            CheckUsernameRequest(channel=InputChannelEmpty(), username=action.username),
        )
        if not available:
            code = "channel_username_occupied"
            raise ChannelGatewayError(code)
    result = await client(
        CreateChannelRequest(
            title=action.title,
            about=action.about,
            broadcast=True,
            megagroup=False,
        ),
    )
    entity = _created_channel(result)
    new_id = int(getattr(entity, "id", 0) or 0) or None
    if action.username is not None:
        try:
            await client(UpdateUsernameRequest(channel=entity, username=action.username))  # ty: ignore[invalid-argument-type]
        except (errors.FloodWaitError, errors.PeerFloodError):
            # Must reach execute's flood ladder unchanged; the created channel
            # still surfaces via list-own-channels on the next refresh.
            raise
        except errors.RPCError as exc:
            # Mapped or not, the channel already exists — the id must ride the
            # failure so the caller can adopt it instead of re-creating.
            mapped = _map_telethon_error(exc)
            code = mapped.code if mapped is not None else "channel_username_assign_failed"
            raise ChannelGatewayError(code, channel_id=new_id) from exc
    return _DispatchResult(channel_id=new_id)


def _created_channel(result: object) -> object:
    """The new ``Channel`` entity out of the ``Updates`` container's chats."""
    for chat in getattr(result, "chats", []) or []:
        if int(getattr(chat, "id", 0) or 0):
            return chat
    code = "channel_create_failed"
    raise ChannelGatewayError(code)


async def _edit_channel(client: TelegramClient, action: EditChannel) -> None:
    """Edit title and/or about. A NotModified answer is an idempotent no-op.

    Telethon raises ``ChatNotModifiedError`` / ``ChatAboutNotModifiedError``
    when the new value equals the current one — re-saving an unchanged form
    field must not fail the whole edit, so both are suppressed. They are
    RPC errors, so the suppression must happen HERE, before the module-level
    RPC mapping (which would let them bubble as generic failures).
    """
    entity = await _input_channel(client, action.channel_id)
    if action.title is not None:
        with suppress(errors.ChatNotModifiedError):
            await client(EditTitleRequest(channel=entity, title=action.title))  # ty: ignore[invalid-argument-type]
    if action.about is not None:
        with suppress(errors.ChatAboutNotModifiedError):
            await client(EditChatAboutRequest(peer=entity, about=action.about))  # ty: ignore[invalid-argument-type]


async def _set_channel_photo(client: TelegramClient, action: SetChannelPhoto) -> None:
    entity = await _input_channel(client, action.channel_id)
    uploaded = await client.upload_file(
        _named_bytes(action.filename, action.content),
        file_name=action.filename,
    )
    await client(
        EditPhotoRequest(
            channel=entity,  # ty: ignore[invalid-argument-type]
            photo=InputChatUploadedPhoto(file=uploaded),
        ),
    )


async def _publish_post(client: TelegramClient, action: PublishChannelPost) -> _DispatchResult:
    entity = await _input_channel(client, action.channel_id)
    if action.media_kind is None:
        message = await client.send_message(entity, action.text)  # ty: ignore[invalid-argument-type]
    elif action.media_kind == "photo":
        message = await client.send_file(
            entity,  # ty: ignore[invalid-argument-type]
            _named_bytes(action.filename or "photo.jpg", action.content or b""),
            caption=action.text,
            file_name=action.filename,
        )
    else:
        message = await _send_video_post(client, entity, action)
    return _DispatchResult(message_id=int(getattr(message, "id", 0) or 0) or None)


async def _send_video_post(
    client: TelegramClient,
    entity: object,
    action: PublishChannelPost,
) -> object:
    """Re-encode through ffmpeg (source resolution) and send as streamable video.

    The explicit thumb, ``mime_type`` and ``DocumentAttributeVideo`` mirror the
    story path: missing any of them makes Telegram treat the upload as a
    generic document instead of an inline-playable video.
    """
    video_bytes, thumb_bytes, duration, width, height = await normalize_channel_video_for_telegram(
        action.content or b"",
    )
    return await client.send_file(
        entity,  # ty: ignore[invalid-argument-type]
        _named_bytes("video.mp4", video_bytes),
        caption=action.text,
        thumb=thumb_bytes,
        mime_type="video/mp4",
        attributes=[
            DocumentAttributeVideo(
                duration=max(int(duration), 1),
                w=width,
                h=height,
                supports_streaming=True,
            ),
        ],
        file_name="video.mp4",
    )


async def _edit_post(client: TelegramClient, action: EditChannelPost) -> None:
    """Edit a post's text. Same-text is a no-op; expired/missing map to codes."""
    entity = await _input_channel(client, action.channel_id)
    try:
        await client.edit_message(entity, action.post_id, action.text)  # ty: ignore[invalid-argument-type]
    except errors.MessageNotModifiedError:
        return
    except errors.MessageEditTimeExpiredError as exc:
        code = "message_edit_time_expired"
        raise ChannelGatewayError(code) from exc
    except errors.MessageIdInvalidError as exc:
        code = "channel_post_not_found"
        raise ChannelGatewayError(code) from exc


def _channel_log_extra(action: TelegramAction) -> dict[str, object]:
    """Compact log summary per channel action — ids and flags, no post text."""
    extra: dict[str, object]
    match action:
        case CreateChannel():
            extra = {"title": action.title, "has_username": action.username is not None}
        case EditChannel():
            extra = {
                "channel_id": action.channel_id,
                "has_title": action.title is not None,
                "has_about": action.about is not None,
            }
        case SetChannelPhoto():
            extra = {"channel_id": action.channel_id, "filename": action.filename}
        case PublishChannelPost():
            extra = {"channel_id": action.channel_id, "media_kind": action.media_kind}
        case EditChannelPost() | DeleteChannelPost():
            extra = {"channel_id": action.channel_id, "post_id": action.post_id}
        case DeleteChannel():
            extra = {"channel_id": action.channel_id}
        case _:  # pragma: no cover - caller only routes channel actions here
            extra = {}
    return extra
