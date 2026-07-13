"""Profile-media actions — set photo, post story, add profile music."""

from __future__ import annotations

import asyncio
import mimetypes
from contextlib import suppress
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageFilter, UnidentifiedImageError
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


class StoryImageNormalisationError(ValueError):
    """Raised when a story image can't be decoded onto the 1080x1920 canvas.

    Mirrors :class:`core.telegram_client._video.StoryVideoNormalisationError`:
    ``str(exc)`` is the stable, locale-neutral code — it survives the
    ``execute`` → ``ActionResult.error_message`` → API error-envelope path as a
    code the SPA translates, never Russian prose (non-negotiable #12).
    """

    def __init__(self) -> None:
        self.code = "story_image_invalid"
        super().__init__(self.code)


class StoryCollageLayoutError(ValueError):
    """Raised when a collage's requested layout id can't be resolved.

    Same contract as :class:`StoryImageNormalisationError`: ``str(exc)`` is the
    stable, locale-neutral code the SPA translates. The unresolvable detail
    (bad id / unsupported count) rides the chained cause into the failure log.
    """

    def __init__(self) -> None:
        self.code = "story_collage_unknown_layout"
        super().__init__(self.code)


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
    story_id = getattr(result, "id", None)
    return story_id if isinstance(story_id, int) else None


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


def _decode_story_source(content: bytes) -> Image.Image:
    """Decode arbitrary upload bytes into an RGB Pillow image, or raise.

    Shared by the single-photo and collage paths so both surface the same
    locale-neutral ``story_image_invalid`` code for undecodable input.
    """
    try:
        with Image.open(BytesIO(content)) as opened:
            opened.load()
            return opened.convert("RGB") if opened.mode != "RGB" else opened.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        # UnidentifiedImageError = container Pillow can't decode (e.g. HEIC/JXL
        # renamed to .png); OSError from load() = truncated/corrupt bytes. The
        # chained cause carries the Pillow reason plus the file's real magic
        # bytes so the telegram_post_story_failed log shows what the file was.
        detail = f"{type(exc).__name__}: {exc}; magic={content[:12].hex()}"
        raise StoryImageNormalisationError from ValueError(detail)


def _normalize_story_image_for_telegram(content: bytes) -> bytes:
    """Compose a photo onto Telegram's 1080x1920 story canvas (JPEG q90).

    The source is fitted into the canvas without cropping; the empty
    margins are filled with a heavily-blurred enlarged copy of the same
    photo, matching how the official Telegram Android client composes
    stories (StoryEntry.java: ``backgroundFile`` is a blurred upscale of
    the source). Solid-color letterbox is functionally accepted by the
    server but looks visibly cheaper than the official UX. Anything
    outside the 9:16 aspect window gets rejected with
    ``PHOTO_INVALID_DIMENSIONS``, so this step is required, not optional.
    """
    target_width, target_height = 1080, 1920
    source = _decode_story_source(content)
    canvas = _blurred_story_background(source, target_width, target_height)
    fitted = source.copy()
    fitted.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    canvas.paste(
        fitted,
        (
            (target_width - fitted.width) // 2,
            (target_height - fitted.height) // 2,
        ),
    )
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=90)
    return output.getvalue()


def _cover_crop(source: Image.Image, width: int, height: int) -> Image.Image:
    """Scale-and-center-crop ``source`` to exactly ``width`` x ``height`` (no bars)."""
    scale = max(width / source.width, height / source.height)
    scaled = source.resize(
        (max(int(source.width * scale), width), max(int(source.height * scale), height)),
        Image.Resampling.LANCZOS,
    )
    left = (scaled.width - width) // 2
    top = (scaled.height - height) // 2
    return scaled.crop((left, top, left + width, top + height))


def _blurred_story_background(
    source: Image.Image,
    target_width: int,
    target_height: int,
) -> Image.Image:
    """Render the blurred-cover fill that goes behind the fitted source.

    Cover-crops the source to the full 1080x1920 canvas, then applies a strong
    Gaussian blur so the edges read as ambient colour rather than a recognisable
    second copy of the image. Mirrors the official Android client's story
    background composition.
    """
    cover = _cover_crop(source, target_width, target_height)
    return cover.filter(ImageFilter.GaussianBlur(radius=50))


# Collage layout templates: photo count → layout id → list of cells, each a
# ``(x, y, w, h)`` rect in fractions of the 1080x1920 canvas. The first layout
# for a count is that count's default when the client omits ``collage_layout``.
# The number of source images must equal the number of cells (guaranteed by
# keying on count; enforced by ``zip(strict=True)`` in the composer).
_THIRD = 1 / 3
_COLLAGE_TEMPLATES: dict[int, dict[str, list[tuple[float, float, float, float]]]] = {
    2: {
        "v2": [(0, 0, 1, 0.5), (0, 0.5, 1, 0.5)],
        "h2": [(0, 0, 0.5, 1), (0.5, 0, 0.5, 1)],
    },
    3: {
        "v3": [(0, 0, 1, _THIRD), (0, _THIRD, 1, _THIRD), (0, 2 * _THIRD, 1, _THIRD)],
        "left1_right2": [(0, 0, 0.5, 1), (0.5, 0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
        "top1_bottom2": [(0, 0, 1, 0.5), (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
    },
    4: {
        "grid2x2": [
            (0, 0, 0.5, 0.5),
            (0.5, 0, 0.5, 0.5),
            (0, 0.5, 0.5, 0.5),
            (0.5, 0.5, 0.5, 0.5),
        ],
        "v4": [(0, 0, 1, 0.25), (0, 0.25, 1, 0.25), (0, 0.5, 1, 0.25), (0, 0.75, 1, 0.25)],
    },
    5: {
        "top2_bottom3": [
            (0, 0, 0.5, 0.5),
            (0.5, 0, 0.5, 0.5),
            (0, 0.5, _THIRD, 0.5),
            (_THIRD, 0.5, _THIRD, 0.5),
            (2 * _THIRD, 0.5, _THIRD, 0.5),
        ],
    },
    6: {
        "grid2x3": [
            (0, 0, 0.5, _THIRD),
            (0.5, 0, 0.5, _THIRD),
            (0, _THIRD, 0.5, _THIRD),
            (0.5, _THIRD, 0.5, _THIRD),
            (0, 2 * _THIRD, 0.5, _THIRD),
            (0.5, 2 * _THIRD, 0.5, _THIRD),
        ],
    },
}


def _collage_cells(count: int, layout: str) -> list[tuple[float, float, float, float]]:
    templates = _COLLAGE_TEMPLATES.get(count)
    if templates is None:
        raise StoryCollageLayoutError from ValueError(f"unsupported collage image count: {count}")
    cells = templates.get(layout)
    if cells is None:
        raise StoryCollageLayoutError from ValueError(
            f"unknown collage layout {layout!r} for {count} images"
        )
    return cells


def _default_collage_layout(count: int) -> str:
    """The first template id for ``count`` — the default when none is requested."""
    templates = _COLLAGE_TEMPLATES.get(count)
    if templates is None:
        raise StoryCollageLayoutError from ValueError(f"unsupported collage image count: {count}")
    return next(iter(templates))


def _compose_story_collage(images: list[bytes], layout: str) -> bytes:
    """Stitch 2..6 photos into one 1080x1920 JPEG (q90) per the ``layout`` template.

    Each source is cover-crop-fitted into its cell rect with a config-driven gap
    between cells, over a blurred background built from the first image. Raises
    ``StoryCollageLayoutError`` when the count is unsupported or the layout id is
    unknown for that count, and ``StoryImageNormalisationError`` on any
    undecodable input.
    """
    target_width, target_height = 1080, 1920
    cells = _collage_cells(len(images), layout)
    sources = [_decode_story_source(image) for image in images]
    canvas = _blurred_story_background(sources[0], target_width, target_height)
    gap = settings.profile_media.story_collage_gap_px
    for source, (x, y, w, h) in zip(sources, cells, strict=True):
        px = round(x * target_width) + gap // 2
        py = round(y * target_height) + gap // 2
        pw = round(w * target_width) - gap
        ph = round(h * target_height) - gap
        if pw > 0 and ph > 0:
            canvas.paste(_cover_crop(source, pw, ph), (px, py))
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


async def _resolve_history_photo(client: TelegramClient, photo_id: int) -> InputPhoto | None:
    """Re-resolve a fresh ``InputPhoto`` (id/access_hash/file_reference) by id.

    A ``file_reference`` from the UI snapshot can be stale — they expire — and a
    stale reference makes ``updateProfilePhoto`` misbehave. Pull the current
    history and rebuild the ``InputPhoto`` from live server data, or ``None`` if
    the id is no longer present.
    """
    result = await client(
        GetUserPhotosRequest(
            user_id=InputUserSelf(),
            offset=0,
            max_id=0,
            limit=settings.profile_media.set_main_history_limit,
        ),
    )
    for photo in getattr(result, "photos", []) or []:
        if int(getattr(photo, "id", 0) or 0) == photo_id:
            return InputPhoto(
                id=photo_id,
                access_hash=int(getattr(photo, "access_hash", 0) or 0),
                file_reference=bytes(getattr(photo, "file_reference", b"") or b""),
            )
    return None


async def _set_main_profile_photo(client: TelegramClient, action: SetMainProfilePhoto) -> None:
    """Promote an existing history photo to the current avatar — no duplicate left.

    Live-confirmed semantics: raw ``photos.updateProfilePhoto`` on a photo
    already in the account's history does NOT reorder it in place — the server
    mints a brand-new photo (fresh id) at the front and leaves the original
    entry behind, so a naive call leaves two identical photos in the history.

    We re-resolve a FRESH ``InputPhoto`` for the target from a live
    ``GetUserPhotos`` (never trusting the possibly-stale snapshot reference),
    promote it, then delete the now-redundant original — but ONLY when the
    server actually minted a new id (``new_id != target``). Because the promote
    rotates every file_reference, the delete re-resolves the leftover original a
    SECOND time (post-promote) and verifies the removal like
    :func:`_remove_profile_photo`. If a server ever reorders in place
    (``new_id == target``) we delete nothing, and we never touch any other photo
    (the previous avatar stays put).
    """
    fresh = await _resolve_history_photo(client, action.photo_id)
    if fresh is None:
        msg = "Target profile photo is no longer in the account's history"
        raise RuntimeError(msg)
    result = await client(UpdateProfilePhotoRequest(id=fresh))
    new_id = getattr(getattr(result, "photo", None), "id", None)
    if isinstance(new_id, int) and new_id != action.photo_id:
        # updateProfilePhoto rotates every photo's file_reference, so the
        # pre-promote `fresh` ref is already stale for the delete (a live run
        # showed the delete silently no-op with it). Re-resolve the leftover
        # original from live history and delete THAT. If it's gone, Telegram
        # moved the photo rather than duplicating it — nothing to clean up.
        superseded = await _resolve_history_photo(client, action.photo_id)
        if superseded is not None:
            deleted = await client(DeletePhotosRequest(id=[superseded]))
            if action.photo_id not in (deleted or []):
                msg = "Telegram did not delete the superseded duplicate photo"
                raise RuntimeError(msg)


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
