"""Profile-media actions — set photo, post story, add profile music."""

from __future__ import annotations

import asyncio
import mimetypes
from contextlib import suppress
from typing import TYPE_CHECKING

from telethon import utils
from telethon.tl.functions.account import SaveMusicRequest
from telethon.tl.functions.photos import (
    DeletePhotosRequest,
    GetUserPhotosRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    InputDocument,
    InputPhoto,
    InputUserSelf,
    UserProfilePhotoEmpty,
)

from core.config import settings
from core.db import update_account_avatar
from core.logging import log_event
from core.telegram_client._io import _named_bytes
from core.telegram_client._media_stories import (
    _post_story,
    _remove_story,
    _toggle_story_pinned,
)
from core.telegram_client._pool import get_client
from core.telegram_client._session import _download_avatar_thumb
from core.telegram_client._story_image import _decode_image_source
from schemas.telegram_actions import (
    AddProfileMusic,
    PostStory,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    RemoveStory,
    SetMainProfilePhoto,
    SetProfilePhoto,
    ToggleStoryPinned,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction


class ProfileGatewayError(ValueError):
    """A profile (field or media) action was refused; ``str(exc)`` is the stable code.

    Same contract as :class:`core.telegram_client._channels.ChannelGatewayError`:
    the code rides ``execute``'s generic-exception ladder into
    ``ActionResult.error_message`` verbatim and the SPA translates it
    (non-negotiable #12). The unreadable detail (Pillow reason, stale-id
    context) travels as the chained cause into the failure log.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def _dispatch_profile_media_action(
    client: TelegramClient,
    action: TelegramAction,
) -> int | None:
    # Only PostStory yields a message_id; every other action is fire-and-forget
    # (single trailing ``return None`` keeps the return count lint-friendly).
    match action:
        case PostStory():
            return await _post_story(client, action)
        case SetProfilePhoto():
            await _set_profile_photo(client, action.filename, action.content)
        case AddProfileMusic():
            await _add_profile_music(client, action)
        case RemoveProfileMusic():
            await _remove_profile_music(client, action)
        case RemoveProfilePhoto():
            await _remove_profile_photo(client, action)
        case SetMainProfilePhoto():
            await _set_main_profile_photo(client, action)
        case RemoveStory():
            await _remove_story(client, action)
        case ToggleStoryPinned():
            await _toggle_story_pinned(client, action)
        case _:  # pragma: no cover - caller only routes media actions here
            msg = f"Unsupported profile media action_type: {action.action_type}"
            raise ValueError(msg)
    return None


def _validate_profile_photo(content: bytes) -> None:
    """Decode gate (mirrors the story path): refuse bytes Pillow can't decode.

    Without it a renamed/corrupt ``.jpg`` travels all the way to Telegram and
    comes back as a raw ``PHOTO_INVALID``-family error instead of a stable code.
    """
    code = "profile_photo_invalid"
    try:
        _decode_image_source(content)
    except ValueError as exc:
        raise ProfileGatewayError(code) from exc


async def _set_profile_photo(client: TelegramClient, filename: str, content: bytes) -> None:
    await asyncio.to_thread(_validate_profile_photo, content)
    uploaded = await client.upload_file(_named_bytes(filename, content), file_name=filename)
    await client(UploadProfilePhotoRequest(file=uploaded))


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
        code = "profile_music_invalid"
        raise ProfileGatewayError(code) from ValueError(
            "Telegram did not return an audio document",
        )
    await client(SaveMusicRequest(id=utils.get_input_document(document)))
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int):
        with suppress(Exception):
            await client.delete_messages("me", [message_id], revoke=True)


async def _remove_profile_music(client: TelegramClient, action: RemoveProfileMusic) -> None:
    """Unpin one track from the account's saved profile music.

    ``account.saveMusic`` is dual-purpose — passing ``unsave=True`` removes
    the document from the saved list. We reuse it instead of pulling a
    separate ``DeleteSavedMusicRequest`` (which Telethon doesn't ship in 1.43.2).

    The server answers ``False`` for a stale/unknown ``InputDocument`` — a
    silent no-op that would otherwise be logged as a successful removal while
    the track stays on the profile. Raising surfaces it instead (mirrors
    ``_remove_profile_photo``'s deleted-vector check).
    """
    removed = await client(
        SaveMusicRequest(
            id=InputDocument(
                id=action.file_id,
                access_hash=action.access_hash,
                file_reference=action.file_reference,
            ),
            unsave=True,
        ),
    )
    if not removed:
        code = "profile_music_stale_reference"
        raise ProfileGatewayError(code) from ValueError(
            "Telegram did not remove the track (unknown or expired reference)",
        )


async def _remove_profile_photo(client: TelegramClient, action: RemoveProfilePhoto) -> None:
    """Drop one photo from the account's profile-photo history.

    ``DeletePhotosRequest`` accepts a list of ``InputPhoto`` and returns the
    vector of ids it actually deleted; if the deleted photo was the current
    avatar, Telegram automatically promotes the next one — no explicit re-set
    needed.

    We verify our id is in that returned vector. Telegram answers a stale or
    unrecognised ``InputPhoto`` with an empty vector — a silent no-op that
    would otherwise be logged as a successful removal while the photo stays on
    the server (this is what let JS-rounded int64 ids "delete" the same photo
    repeatedly). Raising turns that into a surfaced error instead.
    """
    deleted = await client(
        DeletePhotosRequest(
            id=[
                InputPhoto(
                    id=action.photo_id,
                    access_hash=action.access_hash,
                    file_reference=action.file_reference,
                ),
            ],
        ),
    )
    if action.photo_id not in (deleted or []):
        code = "profile_photo_stale_reference"
        raise ProfileGatewayError(code) from ValueError(
            "Telegram did not delete the photo (unknown or expired reference)",
        )


async def _history_photos(client: TelegramClient) -> list[object]:
    """Fetch the live profile-photo history page (newest first)."""
    result = await client(
        GetUserPhotosRequest(
            user_id=InputUserSelf(),
            offset=0,
            max_id=0,
            limit=settings.profile_media.set_main_history_limit,
        ),
    )
    return list(getattr(result, "photos", []) or [])


def _photo_ids(photos: list[object]) -> list[int]:
    return [int(getattr(photo, "id", 0) or 0) for photo in photos]


def _find_history_photo(photos: list[object], photo_id: int) -> object | None:
    """The raw history ``Photo`` object by id, from live server data.

    A ``file_reference`` from the UI snapshot can be stale — they expire — so
    the target is always re-resolved from a fresh ``GetUserPhotos`` read;
    ``None`` if the id is no longer present.
    """
    for photo in photos:
        if int(getattr(photo, "id", 0) or 0) == photo_id:
            return photo
    return None


async def _current_avatar_id(client: TelegramClient) -> int | None:
    """The current avatar's photo id per ``users.getFullUser`` (authoritative)."""
    full = await client(GetFullUserRequest(InputUserSelf()))
    photo_id = getattr(getattr(getattr(full, "full_user", None), "profile_photo", None), "id", None)
    return photo_id if isinstance(photo_id, int) else None


async def _set_main_profile_photo(client: TelegramClient, action: SetMainProfilePhoto) -> None:
    """Make a history photo the avatar by RE-UPLOADING its bytes as a new photo.

    The official promote (``photos.updateProfilePhoto``) mints a new id that
    INHERITS the original photo's upload date, so viewers see the new main
    mid-carousel instead of first, and clients with a cached gallery show the
    old and new id of the same image side by side until they refetch (live
    repro 2026-07-15). Re-uploading the same bytes as a NEW photo gives it a
    fresh date — first slide everywhere, plain "user picked a new avatar"
    semantics with no id replacement to confuse caches.

    The original stays in the history as a visible duplicate; the operator
    deletes it from the dashboard if unwanted. This action itself deletes
    NOTHING — an earlier auto-"dedup" here destroyed the unrelated previous
    main avatar on a live account (debug.log, 2026-07-13); permanent data
    loss, never again.

    The id flow is still logged before/after (``telegram_set_main_id_flow``)
    so a live run is verifiable from ``debug.log`` — ``promoted_photo_id`` is
    the freshly uploaded photo's id and should match the new
    ``current_avatar_id``.
    """
    photos = await _history_photos(client)
    await log_event(
        "INFO",
        "telegram_set_main_id_flow",
        extra={
            "phase": "before",
            "target_photo_id": action.photo_id,
            "history_ids": _photo_ids(photos),
            "current_avatar_id": await _current_avatar_id(client),
        },
    )
    target = _find_history_photo(photos, action.photo_id)
    if target is None:
        code = "profile_photo_not_found"
        raise ProfileGatewayError(code) from ValueError(
            "Target profile photo is no longer in the account's history",
        )
    # Full-size download (no thumb arg = largest stored size, the same
    # rendition every viewer sees), then the standard new-avatar upload.
    data = await client.download_media(target, file=bytes)  # ty: ignore[invalid-argument-type]
    if not isinstance(data, (bytes, bytearray)) or not data:
        code = "profile_photo_download_failed"
        raise ProfileGatewayError(code) from ValueError(
            "Telegram did not return the photo bytes",
        )
    uploaded = await client.upload_file(
        _named_bytes("avatar.jpg", bytes(data)),
        file_name="avatar.jpg",
    )
    result = await client(UploadProfilePhotoRequest(file=uploaded))
    new_id = getattr(getattr(result, "photo", None), "id", None)
    # The "after" phase logs only what this call already knows — re-fetching
    # history + full-user here cost 2 RPCs per click purely for the debug log.
    await log_event(
        "INFO",
        "telegram_set_main_id_flow",
        extra={
            "phase": "after",
            "target_photo_id": action.photo_id,
            "promoted_photo_id": new_id if isinstance(new_id, int) else None,
        },
    )


async def refresh_account_avatar(account_id: str) -> None:
    """Re-sync the accounts-list avatar (``avatar_thumb``/``avatar_etag``) from Telegram.

    Called by the service layer after a photo mutation (set / remove / set-main)
    so the list row shows the new avatar immediately instead of waiting for the
    next session check. Mirrors the check's avatar capture: fresh ``get_me()``
    (the pooled entity cache may still hold the pre-mutation photo), then the
    ~160px thumb via ``_download_avatar_thumb``. Best-effort — any refusal is
    logged and swallowed; a refused *download* keeps the cached thumb (the
    session-check rule), while a genuinely absent photo clears it.
    """
    try:
        client = await get_client(account_id)
        me = await client.get_me()
        photo = getattr(me, "photo", None)
        has_photo = photo is not None and not isinstance(photo, UserProfilePhotoEmpty)
        thumb = await _download_avatar_thumb(client, me) if has_photo else None
    except Exception as exc:  # noqa: BLE001 - the avatar is cosmetic; the mutation already succeeded.
        await log_event(
            "WARNING",
            "account_avatar_refresh_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )
        return
    if has_photo and thumb is None:
        return
    await update_account_avatar(account_id, thumb)
