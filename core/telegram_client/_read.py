"""Read-only Telegram gateway — pulls live profile state for the UI.

Parallel to ``_actions.execute``: keeps the write-action discriminated union
clean and avoids growing ``ActionResult`` into a sum type that has to model
both "I sent a message (message_id)" and "here is the user's bio (string)".

Music read is gated behind a runtime import of Telethon's
``GetSavedMusicRequest`` — added in TL layer 213 (Aug 2025) and only present in
recent Telethon releases. When the import fails, the dispatcher returns an
empty result with ``supported=False`` so the UI can hide the music block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from telethon import errors
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.functions.stories import GetPeerStoriesRequest, GetPinnedStoriesRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    InputPeerSelf,
    InputUserSelf,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.telegram_client._pool import get_client
from schemas.telegram_actions import (
    GetUserProfile,
    ListActiveStories,
    ListPinnedStories,
    ListProfileMusic,
    ListProfilePhotos,
)
from schemas.telegram_profile_snapshot import (
    TelegramActiveStories,
    TelegramMusicItem,
    TelegramPinnedStories,
    TelegramProfileMusic,
    TelegramProfilePhoto,
    TelegramProfilePhotos,
    TelegramProfileSnapshot,
    TelegramStoryThumb,
)

if TYPE_CHECKING:
    from pydantic import BaseModel
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramReadAction

# Music read landed in TL layer 213 (2025-08). Telethon ≥1.43 ships
# ``GetSavedMusicRequest``; the import is optional so older installs degrade
# silently instead of crashing the whole dialog open.
try:  # pragma: no cover - branch depends on installed Telethon version
    from telethon.tl.functions.users import GetSavedMusicRequest as _GetSavedMusicRequest

    GetSavedMusicRequest: type | None = _GetSavedMusicRequest
    _MUSIC_API_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on older Telethon
    GetSavedMusicRequest = None
    _MUSIC_API_AVAILABLE = False


class TelegramAccountNotFoundError(LookupError):
    """Raised when ``execute_read`` can't find the account in the DB."""


class TelegramReadError(RuntimeError):
    """Wraps a Telethon failure so callers don't need to import telethon.

    Keeps the layer boundary clean: :mod:`services/` catches
    ``TelegramReadError`` and never sees ``telethon.errors.*`` directly.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def execute_read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """Dispatch a single read action — convenience wrapper around ``execute_read_many``."""
    results = await execute_read_many(account_id, [action])
    return results[0]


async def execute_read_many(
    account_id: str,
    actions: list[TelegramReadAction],
) -> list[BaseModel]:
    """Dispatch multiple read actions on the per-account pooled client.

    The dialog needs three reads (profile + stories + music) per open.
    Before the pool landed, each read opened its own Telethon client on the
    same ``.session`` SQLite file and the three concurrent ``fetch_account``
    reads on ``telebuba.db`` raced into ``OperationalError: database is
    locked`` under live warming-runtime load. With the pool, one persistent
    client serves both the dialog and the warming task; actions still run
    sequentially in input order on the single MTProto connection.

    Telethon errors (FloodWait, RPC, etc.) are caught and re-raised as
    :class:`TelegramReadError` so the service layer can handle them without
    importing telethon (layer isolation, non-negotiable #5).
    """
    account = await fetch_account(account_id)
    if account is None:
        msg = f"Account not found: {account_id}"
        raise TelegramAccountNotFoundError(msg)

    try:
        client = await get_client(account_id)
        results: list[BaseModel] = [
            await _dispatch_read_action(client, action) for action in actions
        ]
    except errors.FloodWaitError as exc:
        reason = f"FloodWait({exc.seconds}s)"
        raise TelegramReadError(reason) from exc
    except errors.RPCError as exc:
        reason = f"RPC: {type(exc).__name__}"
        raise TelegramReadError(reason) from exc
    else:
        return results


async def _dispatch_read_action(
    client: TelegramClient,
    action: TelegramReadAction,
) -> BaseModel:
    match action:
        case GetUserProfile():
            return await _dispatch_get_user_profile(client)
        case ListPinnedStories():
            return await _dispatch_list_pinned_stories(client, action)
        case ListActiveStories():
            return await _dispatch_list_active_stories(client)
        case ListProfileMusic():
            return await _dispatch_list_profile_music(client)
        case ListProfilePhotos():
            return await _dispatch_list_profile_photos(client, action)
        case _:  # pragma: no cover - discriminated union is exhaustive
            msg = f"Unsupported read action_type: {action.action_type}"
            raise ValueError(msg)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _dispatch_get_user_profile(client: TelegramClient) -> TelegramProfileSnapshot:
    full = await client(GetFullUserRequest(InputUserSelf()))
    full_user = getattr(full, "full_user", None)
    bio = _optional_str(getattr(full_user, "about", None))
    users = getattr(full, "users", []) or []
    user = users[0] if users else None
    avatar_bytes = await _download_self_avatar(client)
    return TelegramProfileSnapshot(
        first_name=_optional_str(getattr(user, "first_name", None)),
        last_name=_optional_str(getattr(user, "last_name", None)),
        username=_optional_str(getattr(user, "username", None)),
        phone=_optional_str(getattr(user, "phone", None)),
        bio=bio,
        avatar_bytes=avatar_bytes,
    )


async def _download_self_avatar(client: TelegramClient) -> bytes | None:
    """Return raw avatar bytes for the signed-in user, or ``None`` if absent."""
    try:
        # Passing the ``bytes`` type (not an instance) makes Telethon return
        # the downloaded payload in memory. The type stub only lists concrete
        # bytes / str / BinaryIO values, hence the ignore.
        data = await client.download_profile_photo("me", file=bytes)  # ty: ignore[invalid-argument-type]
    except errors.RPCError:
        # Some accounts return a photo ref that can't be downloaded (e.g.
        # privacy-restricted self-photos). Treat as "no avatar" rather than
        # killing the whole snapshot fetch.
        return None
    return data if isinstance(data, (bytes, bytearray)) else None


async def _dispatch_list_pinned_stories(
    client: TelegramClient,
    action: ListPinnedStories,
) -> TelegramPinnedStories:
    result = await client(
        GetPinnedStoriesRequest(peer=InputPeerSelf(), offset_id=0, limit=action.limit),
    )
    raw_stories = getattr(result, "stories", []) or []
    items = [await _story_thumb(client, story, is_pinned=True) for story in raw_stories]
    return TelegramPinnedStories(items=[item for item in items if item is not None])


async def _dispatch_list_active_stories(client: TelegramClient) -> TelegramActiveStories:
    """Pull the account's currently-active (≤24 h) stories.

    ``stories.getPeerStories`` returns ``stories.PeerStories`` whose outer
    ``stories`` attribute is a ``PeerStories`` constructor — the actual
    ``StoryItem`` list lives one level deeper at ``result.stories.stories``.
    Each item's ``pinned`` flag preserves whether it's also profile-pinned,
    so the service layer can dedupe against ``ListPinnedStories`` without
    a second round-trip.
    """
    result = await client(GetPeerStoriesRequest(peer=InputPeerSelf()))
    outer = getattr(result, "stories", None)
    raw_stories = getattr(outer, "stories", []) or []
    items = [
        await _story_thumb(client, story, is_pinned=bool(getattr(story, "pinned", False)))
        for story in raw_stories
    ]
    return TelegramActiveStories(items=[item for item in items if item is not None])


async def _story_thumb(
    client: TelegramClient,
    story: object,
    *,
    is_pinned: bool,
) -> TelegramStoryThumb | None:
    """Build a snapshot row from a Telethon ``StoryItem``.

    Returns ``None`` for items without an ``id`` so the caller can skip them
    instead of carrying placeholder rows downstream. ``is_active`` is derived
    from ``expire_date`` — the pinned-stories endpoint returns expired items
    too, while the peer-stories endpoint only returns active ones.
    """
    story_id = int(getattr(story, "id", 0) or 0)
    if story_id == 0:
        return None
    return TelegramStoryThumb(
        story_id=story_id,
        kind=_story_kind(story),
        caption=_optional_str(getattr(story, "caption", None)),
        thumb_bytes=await _download_story_thumb(client, story),
        date_unix=_story_date_unix(story),
        is_pinned=is_pinned,
        is_active=_story_is_active(story),
    )


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
    import time  # noqa: PLC0415 — local import keeps the top-level imports lean

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
    inside the carousel slide.
    """
    media = getattr(story, "media", None)
    if media is None:
        return None
    try:
        # ``file=bytes`` (the type) is Telethon's in-memory mode; the stub
        # under-specifies the union so ty needs the override here.
        data = await client.download_media(media, file=bytes, thumb=-1)  # ty: ignore[invalid-argument-type]
    except (errors.RPCError, ValueError, TypeError):
        # Some media kinds reject thumbnail download (privacy-restricted,
        # cache evicted) — the UI shows a placeholder instead of crashing
        # the whole dialog open.
        return None
    return data if isinstance(data, (bytes, bytearray)) else None


async def _dispatch_list_profile_music(client: TelegramClient) -> TelegramProfileMusic:
    if not _MUSIC_API_AVAILABLE or GetSavedMusicRequest is None:
        await log_event("INFO", "telegram_list_profile_music_unsupported")
        return TelegramProfileMusic(items=[], supported=False)
    result = await client(
        GetSavedMusicRequest(
            id=InputUserSelf(),
            offset=0,
            limit=settings.profile_media.music_preview_limit,
            hash=0,
        ),
    )
    documents = getattr(result, "documents", []) or []
    items: list[TelegramMusicItem] = []
    for document in documents:
        file_id = int(getattr(document, "id", 0) or 0)
        if file_id == 0:
            continue
        audio = _find_audio_attribute(document)
        items.append(
            TelegramMusicItem(
                file_id=file_id,
                title=_optional_str(getattr(audio, "title", None)),
                performer=_optional_str(getattr(audio, "performer", None)),
                duration_seconds=int(getattr(audio, "duration", 0) or 0) or None,
                access_hash=int(getattr(document, "access_hash", 0) or 0),
                file_reference=bytes(getattr(document, "file_reference", b"") or b""),
            ),
        )
    return TelegramProfileMusic(items=items, supported=True)


def _find_audio_attribute(document: object) -> object | None:
    for attribute in getattr(document, "attributes", []) or []:
        if isinstance(attribute, DocumentAttributeAudio):
            return attribute
    return None


async def _dispatch_list_profile_photos(
    client: TelegramClient,
    action: ListProfilePhotos,
) -> TelegramProfilePhotos:
    """Pull the account's profile-photo history with a small thumb per photo.

    ``GetUserPhotosRequest`` returns newest-first; the first item is the
    photo Telegram currently shows as the avatar. Each photo carries the
    ``InputPhoto`` id triple needed for the matching ``RemoveProfilePhoto``
    write action.
    """
    result = await client(
        GetUserPhotosRequest(
            user_id=InputUserSelf(),
            offset=0,
            max_id=0,
            limit=action.limit,
        ),
    )
    raw_photos = getattr(result, "photos", []) or []
    items: list[TelegramProfilePhoto] = []
    for photo in raw_photos:
        photo_id = int(getattr(photo, "id", 0) or 0)
        if photo_id == 0:
            continue
        items.append(
            TelegramProfilePhoto(
                photo_id=photo_id,
                access_hash=int(getattr(photo, "access_hash", 0) or 0),
                file_reference=bytes(getattr(photo, "file_reference", b"") or b""),
                date_unix=_photo_date_unix(photo),
                thumb_bytes=await _download_photo_thumb(client, photo),
            ),
        )
    return TelegramProfilePhotos(items=items)


def _photo_date_unix(photo: object) -> int:
    """Coerce Telethon's ``photo.date`` (a ``datetime``) into a Unix int."""
    raw = getattr(photo, "date", None)
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


async def _download_photo_thumb(client: TelegramClient, photo: object) -> bytes | None:
    """Pull the largest cached preview for a profile photo.

    ``thumb=-1`` selects the largest available size in ``photo.sizes`` —
    for profile photos that's the 640 px ``c`` variant, which renders
    crisp inside the 360 px carousel slide (and on retina). ``thumb=0``
    used to fetch the ~160 px stripped preview but stretching it 2x came
    out visibly pixelated.
    """
    try:
        # ``file=bytes`` (the type) is Telethon's in-memory download mode.
        data = await client.download_media(photo, file=bytes, thumb=-1)  # ty: ignore[invalid-argument-type]
    except (errors.RPCError, ValueError, TypeError):
        return None
    return data if isinstance(data, (bytes, bytearray)) else None
