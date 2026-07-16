"""Story dispatchers + StoryItem helpers — extracted from ``_read.py``.

Pinned- and active-stories endpoints, the shared ``StoryItem`` →
``TelegramStoryThumb`` builder, and the privacy / date / activity / kind
helpers. Kept in their own module so ``_read.py`` stays under the
aislop file-size budget after the stories tab grew (active-stories
endpoint, privacy preset, double-nested ``stories.stories`` unwrap).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Literal

from telethon import errors
from telethon.tl.functions.stories import (
    GetPeerStoriesRequest,
    GetPinnedStoriesRequest,
    ReadStoriesRequest,
)
from telethon.tl.types import InputPeerSelf, MessageMediaDocument, MessageMediaPhoto

from core.telegram_client._thumbs import download_thumb_bounded, thumb_limiter
from schemas.telegram_profile_snapshot import (
    StoryPrivacyPreset,
    TelegramActiveStories,
    TelegramPinnedStories,
    TelegramStoryThumb,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import ListPinnedStories, WatchPeerStories


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def dispatch_list_pinned_stories(
    client: TelegramClient,
    action: ListPinnedStories,
) -> TelegramPinnedStories:
    result = await client(
        GetPinnedStoriesRequest(peer=InputPeerSelf(), offset_id=0, limit=action.limit),
    )
    raw_stories = getattr(result, "stories", []) or []
    # Download thumbnails concurrently (serial awaits made the modal open scale
    # linearly with story count) but bounded — see ``_thumbs``.
    semaphore, flood_stop = thumb_limiter()
    items = await asyncio.gather(
        *(
            _story_thumb(client, story, is_pinned=True, semaphore=semaphore, flood_stop=flood_stop)
            for story in raw_stories
        ),
    )
    return TelegramPinnedStories(items=[item for item in items if item is not None])


async def dispatch_watch_peer_stories(client: TelegramClient, action: WatchPeerStories) -> int:
    """View a subscribed peer's active stories and mark them seen.

    ``stories.getPeerStories`` returns ``stories.PeerStories`` whose actual
    ``StoryItem`` list sits one level deeper at ``result.stories.stories``
    (same double-nesting as ``dispatch_list_active_stories``). We mark
    everything up to the newest id read; a peer with no active stories is a
    silent no-op. Returns how many active stories were seen (0 = none), so the
    activity log can say "watched N" vs "peer had no stories".
    """
    peer = await client.get_input_entity(action.peer)
    result = await client(GetPeerStoriesRequest(peer=peer))
    outer = getattr(result, "stories", None)
    raw_stories = getattr(outer, "stories", []) or []
    ids = [int(getattr(story, "id", 0) or 0) for story in raw_stories]
    ids = [story_id for story_id in ids if story_id]
    if ids:
        await client(ReadStoriesRequest(peer=peer, max_id=max(ids)))
    return len(ids)


async def dispatch_list_active_stories(client: TelegramClient) -> TelegramActiveStories:
    """Pull the account's currently-active (≤24 h) stories.

    ``stories.getPeerStories`` returns ``stories.PeerStories`` whose outer
    ``stories`` attribute is itself a ``PeerStories`` constructor — the
    actual ``StoryItem`` list lives one level deeper at
    ``result.stories.stories``. Each item's ``pinned`` flag preserves
    whether it's also profile-pinned, so the service layer can dedupe
    against ``ListPinnedStories`` without a second round-trip.
    """
    result = await client(GetPeerStoriesRequest(peer=InputPeerSelf()))
    outer = getattr(result, "stories", None)
    raw_stories = getattr(outer, "stories", []) or []
    semaphore, flood_stop = thumb_limiter()
    items = await asyncio.gather(
        *(
            _story_thumb(
                client,
                story,
                is_pinned=bool(getattr(story, "pinned", False)),
                semaphore=semaphore,
                flood_stop=flood_stop,
            )
            for story in raw_stories
        ),
    )
    return TelegramActiveStories(items=[item for item in items if item is not None])


async def _story_thumb(
    client: TelegramClient,
    story: object,
    *,
    is_pinned: bool,
    semaphore: asyncio.Semaphore,
    flood_stop: asyncio.Event,
) -> TelegramStoryThumb | None:
    """Build a snapshot row from a Telethon ``StoryItem``.

    Returns ``None`` for items without an ``id`` so the caller can skip
    them instead of carrying placeholder rows downstream.
    """
    story_id = int(getattr(story, "id", 0) or 0)
    if story_id == 0:
        return None
    return TelegramStoryThumb(
        story_id=story_id,
        kind=_story_kind(story),
        caption=_optional_str(getattr(story, "caption", None)),
        thumb_bytes=await download_thumb_bounded(
            semaphore,
            flood_stop,
            "stories",
            lambda: _download_story_thumb(client, story),
        ),
        date_unix=_story_date_unix(story),
        is_pinned=is_pinned,
        is_active=_story_is_active(story),
        privacy_preset=_story_privacy_preset(story),
        views=_story_views(story),
        reactions=_story_reactions(story),
    )


def _story_views(story: object) -> int | None:
    """Read ``StoryItem.views.views_count``; ``None`` when Telegram omits it."""
    count = getattr(getattr(story, "views", None), "views_count", None)
    return int(count) if isinstance(count, int) else None


def _story_reactions(story: object) -> int | None:
    """Read ``StoryItem.views.reactions_count``; ``None`` when Telegram omits it."""
    count = getattr(getattr(story, "views", None), "reactions_count", None)
    return int(count) if isinstance(count, int) else None


def _story_privacy_preset(story: object) -> StoryPrivacyPreset:
    """Map Telegram's flag-based privacy bits to a single preset string.

    Order matters: ``public`` wins over everything (broadest audience),
    then ``close_friends`` (deliberate narrowing), then
    ``selected_contacts`` (custom list), then ``contacts`` (the implicit
    default). Stories with only a raw ``privacy`` rule vector and none of
    the convenience flags fall through to ``unknown``.
    """
    if bool(getattr(story, "public", False)):
        return "public"
    if bool(getattr(story, "close_friends", False)):
        return "close_friends"
    if bool(getattr(story, "selected_contacts", False)):
        return "selected_contacts"
    if bool(getattr(story, "contacts", False)):
        return "contacts"
    return "unknown"


def _story_date_unix(story: object) -> int:
    """Flatten Telethon's ``StoryItem.date`` (a ``datetime``) into a Unix int."""
    raw = getattr(story, "date", None)
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    timestamp = getattr(raw, "timestamp", None)
    if callable(timestamp):
        try:
            return int(timestamp())
        except (TypeError, ValueError):
            return 0
    return 0


def _story_is_active(story: object) -> bool:
    """True when ``StoryItem.expire_date`` is still in the future."""
    expire = getattr(story, "expire_date", None)
    if expire is None:
        return False
    if isinstance(expire, int):
        return expire > int(time.time())
    timestamp = getattr(expire, "timestamp", None)
    if callable(timestamp):
        try:
            return timestamp() > time.time()
        except (TypeError, ValueError):
            return False
    return False


def _story_kind(story: object) -> Literal["image", "video", "unknown"]:
    media = getattr(story, "media", None)
    if isinstance(media, MessageMediaPhoto):
        return "image"
    if isinstance(media, MessageMediaDocument):
        document = getattr(media, "document", None)
        mime = str(getattr(document, "mime_type", "") or "")
        if mime.startswith("video/"):
            return "video"
    return "unknown"


async def _download_story_thumb(client: TelegramClient, story: object) -> bytes | None:
    """Pull the largest cached preview for a story's media.

    ``thumb=-1`` selects the largest available size — for photo stories
    that's the ``c`` 640 px variant, for video stories the largest
    document thumbnail (typically ~320 px). ``thumb=0`` (the smallest
    stripped preview, ~160 px) was visibly pixelated when stretched
    inside the 112 px poster card.
    """
    media = getattr(story, "media", None)
    if media is None:
        return None
    try:
        # ``file=bytes`` (the type) is Telethon's in-memory mode; the stub
        # under-specifies the union so ty needs the override here.
        data = await client.download_media(media, file=bytes, thumb=-1)  # ty: ignore[invalid-argument-type]
    except errors.FloodWaitError:
        # Rate limits must reach the batch breaker in ``_thumbs`` — swallowing
        # them here let sibling downloads keep hammering a flooded connection.
        raise
    except (errors.RPCError, ValueError, TypeError):
        # Some media kinds reject thumbnail download (privacy-restricted,
        # cache evicted) — the UI shows a placeholder instead of crashing
        # the whole dialog open.
        return None
    return data if isinstance(data, (bytes, bytearray)) else None
