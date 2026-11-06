"""Story write-actions — post, remove, and pin/unpin profile stories.

Extracted from ``_media.py`` so the gateway module stays under the aislop
file-size gate; the functions themselves are unchanged. Reached through
``_dispatch_profile_media_action`` in ``_media.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from telethon import utils
from telethon.tl.functions.stories import (
    CanSendStoryRequest,
    DeleteStoriesRequest,
    SendStoryRequest,
    TogglePinnedRequest,
)
from telethon.tl.types import (
    DocumentAttributeVideo,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    InputMessageEntityMentionName,
    InputPeerSelf,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowCloseFriends,
    InputPrivacyValueAllowContacts,
)

from core.telegram_client._io import _named_bytes
from core.telegram_client._story_image import (
    _compose_story_collage,
    _default_collage_layout,
    _normalize_story_image_for_telegram,
)
from core.telegram_client._story_link import (
    LINK_STICKER_CENTRE,
    caption_entities,
    caption_with_mentions,
)
from core.telegram_client._story_stickers import (
    can_render_label,
    sticker_overlay_png,
    stickers_on_photo,
)
from core.telegram_client._video import normalize_story_video_for_telegram
from core.telegram_client._video_args import STORY_VIDEO_SIZE
from schemas.story_design import STORY_LABEL_MAX, StoryLinkSticker

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.tl.types import (
        TypeInputMedia,
        TypeInputPrivacyRule,
        TypeMediaArea,
        TypeMessageEntity,
    )

    from schemas.telegram_actions import PostStory, RemoveStory, ToggleStoryPinned
    from schemas.telegram_actions_media import StoryLink


_FALLBACK_LINK_LABEL = "LINK"


async def _post_story(client: TelegramClient, action: PostStory) -> int | None:
    peer = await client.get_input_entity("me")
    await client(CanSendStoryRequest(peer=peer))
    media, areas = await _story_media(client, action)
    caption, spans = caption_with_mentions(action.caption, action.mentions)
    # Caption formatting first, mentions after it: the formatting offsets stay valid.
    entities: list[TypeMessageEntity] = caption_entities(action.entities, action.caption)
    entities.extend(
        [
            InputMessageEntityMentionName(
                offset=span.offset,
                length=span.length,
                user_id=utils.get_input_user(await client.get_input_entity(span.user_id)),
            )
            for span in spans
        ]
    )
    result = await client(
        SendStoryRequest(
            peer=peer,
            media=media,
            privacy_rules=_story_privacy_rules(action.privacy_preset),
            caption=caption,
            entities=entities or None,
            media_areas=areas or None,
            period=action.period_seconds,
            noforwards=action.protect_content,
            pinned=action.pinned or None,
        ),
    )
    return _story_id_from_updates(result)


def _link_sticker(link: StoryLink) -> StoryLinkSticker:
    """The masstagging link as one white sticker, where its plate has always been."""
    # The operator's words, else the host, else a plain ASCII word every font can draw
    # (an IDN host may need glyphs no installed font has).
    host = urlsplit(link.url).hostname or link.url
    label = next(
        (text for text in (link.text.strip(), host) if text and can_render_label(text)),
        _FALLBACK_LINK_LABEL,
    )
    x, y = LINK_STICKER_CENTRE
    return StoryLinkSticker(url=link.url, label=label[:STORY_LABEL_MAX], x=x, y=y)


def _stickers_of(action: PostStory) -> list[StoryLinkSticker]:
    stickers = list(action.link_stickers)
    if action.link is not None:
        stickers.append(_link_sticker(action.link))
    return stickers


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
) -> tuple[TypeInputMedia, list[TypeMediaArea]]:
    """The uploaded media and the tap areas of the stickers drawn into it."""
    stickers = _stickers_of(action)
    areas: list[TypeMediaArea] = []
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
        if stickers:
            content, drawn = await asyncio.to_thread(stickers_on_photo, content, stickers)
            areas.extend(drawn)
        uploaded = await client.upload_file(
            _named_bytes(action.filename, content),
            file_name=action.filename,
        )
        return InputMediaUploadedPhoto(file=uploaded), areas
    # Video story — re-encode through ffmpeg to H.264/AAC MP4 at 720x1280
    # (matches the Android client) and pass an explicit JPEG thumbnail so
    # inline previews don't render as a black frame. The mime_type and
    # supports_streaming flag are both mandatory for stories: missing either
    # makes Telegram treat the upload as a generic document, not a video.
    overlay: bytes | None = None
    if stickers:
        overlay, drawn = await asyncio.to_thread(sticker_overlay_png, stickers, STORY_VIDEO_SIZE)
        areas.extend(drawn)
    video_bytes, thumb_bytes, duration, width, height = await normalize_story_video_for_telegram(
        action.content, overlay_png=overlay
    )
    uploaded_video = await client.upload_file(
        _named_bytes("story.mp4", video_bytes),
        file_name="story.mp4",
    )
    uploaded_thumb = await client.upload_file(
        _named_bytes("thumb.jpg", thumb_bytes),
        file_name="thumb.jpg",
    )
    media = InputMediaUploadedDocument(
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
    return media, areas


def _story_privacy_rules(preset: str) -> list[TypeInputPrivacyRule]:
    rules: dict[str, list[TypeInputPrivacyRule]] = {
        "public": [InputPrivacyValueAllowAll()],
        "close_friends": [InputPrivacyValueAllowCloseFriends()],
    }
    return rules.get(preset, [InputPrivacyValueAllowContacts()])


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
