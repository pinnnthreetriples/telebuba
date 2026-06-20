"""Profile-media actions — set photo, post story, add profile music."""

from __future__ import annotations

import mimetypes
from contextlib import suppress
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError
from telethon import utils
from telethon.tl.functions.account import SaveMusicRequest
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.functions.stories import CanSendStoryRequest, SendStoryRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    InputDocument,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    InputPhoto,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowCloseFriends,
    InputPrivacyValueAllowContacts,
)

from schemas.telegram_actions import (
    AddProfileMusic,
    PostStory,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    SetProfilePhoto,
)

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.tl.types import TypeInputMedia, TypeInputPrivacyRule

    from schemas.telegram_actions import TelegramAction


async def _dispatch_profile_media_action(
    client: TelegramClient,
    action: TelegramAction,
) -> int | None:
    match action:
        case SetProfilePhoto():
            await _set_profile_photo(client, action.filename, action.content)
            return None
        case PostStory():
            return await _post_story(client, action)
        case AddProfileMusic():
            await _add_profile_music(client, action)
            return None
        case RemoveProfileMusic():
            await _remove_profile_music(client, action)
            return None
        case RemoveProfilePhoto():
            await _remove_profile_photo(client, action)
            return None
        case _:  # pragma: no cover - caller only routes media actions here
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
    if media_kind == "image":
        # Telegram rejects story photos that don't match its narrow aspect
        # window with PHOTO_INVALID_DIMENSIONS. Telethon's send_file resize
        # only enforces the chat-photo 1280 px cap, and we go through the
        # lower-level upload_file path that skips it entirely — so we have
        # to normalise to 1080x1920 ourselves before the upload.
        content = _normalize_story_image_for_telegram(content)
        uploaded = await client.upload_file(_named_bytes(filename, content), file_name=filename)
        return InputMediaUploadedPhoto(file=uploaded)
    uploaded = await client.upload_file(_named_bytes(filename, content), file_name=filename)
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


def _normalize_story_image_for_telegram(content: bytes) -> bytes:
    """Pad and resize a photo to Telegram's story canvas (1080x1920 JPEG).

    Scales the source to fit inside 1080x1920 keeping its aspect ratio, then
    centers it on a black canvas — no cropping, the operator's framing
    survives intact. Anything else gets rejected server-side with
    ``PHOTO_INVALID_DIMENSIONS`` regardless of how big or small the original
    was. JPEG quality 90 keeps the file in the ~200-400 KB range.
    """
    target_width, target_height = 1080, 1920
    try:
        with Image.open(BytesIO(content)) as opened:
            opened.load()
            source = opened.convert("RGB") if opened.mode != "RGB" else opened.copy()
        source.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (target_width, target_height), (0, 0, 0))
        offset = (
            (target_width - source.width) // 2,
            (target_height - source.height) // 2,
        )
        canvas.paste(source, offset)
    except UnidentifiedImageError as exc:
        msg = "Изображение не удалось прочитать — выберите JPG/PNG/WebP"
        raise ValueError(msg) from exc
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=90)
    return output.getvalue()


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
    """
    await client(
        SaveMusicRequest(
            id=InputDocument(
                id=action.file_id,
                access_hash=action.access_hash,
                file_reference=action.file_reference,
            ),
            unsave=True,
        ),
    )


async def _remove_profile_photo(client: TelegramClient, action: RemoveProfilePhoto) -> None:
    """Drop one photo from the account's profile-photo history.

    ``DeletePhotosRequest`` accepts a list of ``InputPhoto``; if the deleted
    photo was the current avatar, Telegram automatically promotes the next
    one — no explicit re-set needed.
    """
    await client(
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


def _named_bytes(filename: str, content: bytes) -> BytesIO:
    stream = BytesIO(content)
    stream.name = filename
    return stream
