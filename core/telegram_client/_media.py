"""Profile-media actions — set photo, post story, add profile music."""

from __future__ import annotations

import asyncio
import mimetypes
from contextlib import suppress
from io import BytesIO
from typing import TYPE_CHECKING

from telethon import utils
from telethon.tl.functions.account import SaveMusicRequest
from telethon.tl.functions.photos import (
    DeletePhotosRequest,
    GetUserPhotosRequest,
    UpdateProfilePhotoRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.functions.stories import (
    CanSendStoryRequest,
    DeleteStoriesRequest,
    SendStoryRequest,
    TogglePinnedRequest,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    InputDocument,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    InputPeerSelf,
    InputPhoto,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowCloseFriends,
    InputPrivacyValueAllowContacts,
    InputUserSelf,
)

from core.config import settings
from core.logging import log_event
from core.telegram_client._story_image import (
    _compose_story_collage,
    _default_collage_layout,
    _normalize_story_image_for_telegram,
)
from core.telegram_client._video import normalize_story_video_for_telegram
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
    from telethon.tl.types import TypeInputMedia, TypeInputPrivacyRule

    from schemas.telegram_actions import TelegramAction


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


async def _set_profile_photo(client: TelegramClient, filename: str, content: bytes) -> None:
    uploaded = await client.upload_file(_named_bytes(filename, content), file_name=filename)
    await client(UploadProfilePhotoRequest(file=uploaded))


async def _post_story(client: TelegramClient, action: PostStory) -> int | None:
    peer = await client.get_input_entity("me")
    await client(CanSendStoryRequest(peer=peer))
    media = await _story_media(client, action)
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
    return _story_id_from_updates(result)


def _story_id_from_updates(result: object) -> int | None:
    """Pull the new story's id out of Telethon's ``Updates`` container.

    ``stories.sendStory`` returns an ``Updates`` (which has no ``.id`` of its
    own — a bare ``getattr(result, "id")`` always came back ``None``); the
    minted id rides inside ``result.updates`` as an ``UpdateStory`` carrying
    ``.story.id``. Guarded iteration: first update with a story id wins.
    """
    updates = getattr(result, "updates", None)
    if not isinstance(updates, (list, tuple)):
        return None
    for update in updates:
        story_id = getattr(getattr(update, "story", None), "id", None)
        if isinstance(story_id, int):
            return story_id
    return None


async def _story_media(
    client: TelegramClient,
    action: PostStory,
) -> TypeInputMedia:
    if action.media_kind == "image":
        # Telegram rejects story photos that don't match its narrow aspect
        # window with PHOTO_INVALID_DIMENSIONS. Telethon's send_file resize
        # only enforces the chat-photo 1280 px cap, and we go through the
        # lower-level upload_file path that skips it entirely — so we have
        # to normalise to 1080x1920 ourselves before the upload.
        if action.extra_images:
            # Multi-photo collage: Telegram has no native multi-photo story API,
            # so we stitch every image into ONE composite photo and send that.
            images = [action.content, *action.extra_images]
            layout = action.collage_layout or _default_collage_layout(len(images))
            content = await asyncio.to_thread(_compose_story_collage, images, layout)
        else:
            content = await asyncio.to_thread(_normalize_story_image_for_telegram, action.content)
        uploaded = await client.upload_file(
            _named_bytes(action.filename, content),
            file_name=action.filename,
        )
        return InputMediaUploadedPhoto(file=uploaded)
    # Video story — re-encode through ffmpeg to H.264/AAC MP4 at 720x1280
    # (matches the Android client) and pass an explicit JPEG thumbnail so
    # inline previews don't render as a black frame. The mime_type and
    # supports_streaming flag are both mandatory for stories: missing either
    # makes Telegram treat the upload as a generic document, not a video.
    video_bytes, thumb_bytes, duration, width, height = await normalize_story_video_for_telegram(
        action.content
    )
    uploaded_video = await client.upload_file(
        _named_bytes("story.mp4", video_bytes),
        file_name="story.mp4",
    )
    uploaded_thumb = await client.upload_file(
        _named_bytes("thumb.jpg", thumb_bytes),
        file_name="thumb.jpg",
    )
    return InputMediaUploadedDocument(
        file=uploaded_video,
        thumb=uploaded_thumb,
        mime_type="video/mp4",
        attributes=[
            DocumentAttributeVideo(
                duration=max(int(duration), 1),
                w=width,
                h=height,
                supports_streaming=True,
            ),
        ],
    )


def _story_privacy_rules(preset: str) -> list[TypeInputPrivacyRule]:
    rules: dict[str, list[TypeInputPrivacyRule]] = {
        "public": [InputPrivacyValueAllowAll()],
        "close_friends": [InputPrivacyValueAllowCloseFriends()],
    }
    return rules.get(preset, [InputPrivacyValueAllowContacts()])


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
        msg = "Telegram did not remove the track (unknown or expired reference)"
        raise RuntimeError(msg)


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
        msg = "Telegram did not delete the photo (unknown or expired reference)"
        raise RuntimeError(msg)


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


def _resolve_history_photo(photos: list[object], photo_id: int) -> InputPhoto | None:
    """Rebuild a fresh ``InputPhoto`` (id/access_hash/file_reference) by id.

    A ``file_reference`` from the UI snapshot can be stale — they expire — and a
    stale reference makes ``updateProfilePhoto`` misbehave. Rebuild the
    ``InputPhoto`` from live server data, or ``None`` if the id is no longer
    present.
    """
    for photo in photos:
        if int(getattr(photo, "id", 0) or 0) == photo_id:
            return InputPhoto(
                id=photo_id,
                access_hash=int(getattr(photo, "access_hash", 0) or 0),
                file_reference=bytes(getattr(photo, "file_reference", b"") or b""),
            )
    return None


async def _current_avatar_id(client: TelegramClient) -> int | None:
    """The current avatar's photo id per ``users.getFullUser`` (authoritative)."""
    full = await client(GetFullUserRequest(InputUserSelf()))
    photo_id = getattr(getattr(getattr(full, "full_user", None), "profile_photo", None), "id", None)
    return photo_id if isinstance(photo_id, int) else None


async def _set_main_profile_photo(client: TelegramClient, action: SetMainProfilePhoto) -> None:
    """Promote an existing history photo to the current avatar. Delete NOTHING.

    True server semantics (official-client parity): ``photos.updateProfilePhoto``
    on a photo already in the history REPLACES it — the original id is consumed
    and a brand-new id is minted at the front; the total count is unchanged and
    the previous main keeps its own id one slot down. TDLib swaps old id → new
    id in its cache and tdesktop does the same in-place replace; NEITHER client
    ever calls ``photos.deletePhotos`` as part of set-as-main — and neither do we.

    A post-promote ``GetUserPhotos`` can still list the consumed original id:
    that is replication lag of the read, not a real leftover. A previous "dedup"
    step here deleted against exactly such a stale view — and because
    own-profile deletes resolve by id alone, it destroyed the UNRELATED previous
    main avatar on a live account (debug.log, 2026-07-13). Permanent data loss;
    never delete anything in this action.

    We only re-resolve a FRESH ``InputPhoto`` for the target (snapshot refs
    expire), promote it, and log the id flow before/after
    (``telegram_set_main_id_flow``) so a live run can be verified from
    ``debug.log`` — the "after" event's ``promoted_photo_id`` is the target's
    NEW identity (tdesktop's old id → new id model) and should match the new
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
    fresh = _resolve_history_photo(photos, action.photo_id)
    if fresh is None:
        msg = "Target profile photo is no longer in the account's history"
        raise RuntimeError(msg)
    result = await client(UpdateProfilePhotoRequest(id=fresh))
    new_id = getattr(getattr(result, "photo", None), "id", None)
    await log_event(
        "INFO",
        "telegram_set_main_id_flow",
        extra={
            "phase": "after",
            "target_photo_id": action.photo_id,
            "history_ids": _photo_ids(await _history_photos(client)),
            "current_avatar_id": await _current_avatar_id(client),
            "promoted_photo_id": new_id if isinstance(new_id, int) else None,
        },
    )


async def _remove_story(client: TelegramClient, action: RemoveStory) -> None:
    """Delete one story (active or pinned — single endpoint covers both).

    Per the official docs, ``stories.deleteStories`` returns the IDs that
    were actually removed. We don't inspect the response: a missing ID
    means the story was already gone (concurrent delete, expired between
    snapshot and click), which is fine for an idempotent operation.
    """
    await client(DeleteStoriesRequest(peer=InputPeerSelf(), id=[action.story_id]))


async def _toggle_story_pinned(client: TelegramClient, action: ToggleStoryPinned) -> None:
    """Pin the story to the profile (kept forever) or unpin it (24 h active only).

    ``stories.togglePinned`` returns the ids it actually toggled; we don't
    inspect it — an already-in-state story yields an empty vector, which is a
    fine no-op for an idempotent toggle.
    """
    await client(
        TogglePinnedRequest(peer=InputPeerSelf(), id=[action.story_id], pinned=action.pinned),
    )


def _named_bytes(filename: str, content: bytes) -> BytesIO:
    stream = BytesIO(content)
    stream.name = filename
    return stream
