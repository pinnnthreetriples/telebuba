"""Profile music / photo-history read dispatchers — extracted from ``_read.py``.

Pure move (precedent: ``_read_stories.py``): the saved-music list and the
profile-photo history reads with their thumbnail/date helpers, kept in their
own module so ``_read.py`` stays under the aislop file-size budget.

The optional ``GetSavedMusicRequest`` import (Telethon ≥ 1.43 only) stays in
``_read.py`` — the availability flag is a patch seam owned there — so the
music dispatcher receives the request class (or ``None``) as an argument.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.types import DocumentAttributeAudio, InputUserSelf

from core.config import settings
from core.logging import log_event
from core.telegram_client._thumbs import download_thumb_bounded, thumb_limiter
from schemas.telegram_profile_snapshot import (
    TelegramMusicItem,
    TelegramProfileMusic,
    TelegramProfilePhoto,
    TelegramProfilePhotos,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import ListProfilePhotos


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def dispatch_list_profile_music(
    client: TelegramClient,
    request_cls: type | None,
) -> TelegramProfileMusic:
    """List the account's saved profile music, or degrade when unsupported.

    ``request_cls`` is ``GetSavedMusicRequest`` when the installed Telethon
    ships it, ``None`` otherwise — passed in by ``_read.py`` so the
    availability flag stays patchable on its owning module.
    """
    if request_cls is None:
        await log_event("INFO", "telegram_list_profile_music_unsupported")
        return TelegramProfileMusic(items=[], supported=False)
    result = await client(
        request_cls(
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


async def dispatch_list_profile_photos(
    client: TelegramClient,
    action: ListProfilePhotos,
) -> TelegramProfilePhotos:
    """Pull the account's profile-photo history with a small thumb per photo.

    ``GetUserPhotosRequest`` returns the history newest-first by date, but the
    first item is NOT necessarily the current avatar: a set-main mints a new
    id that inherits the ORIGINAL photo's date, so the avatar can sit anywhere
    in the list — ``UserFull.profile_photo.id`` is the only avatar authority
    (see ``_dispatch_get_user_profile``). Each photo carries the ``InputPhoto``
    id triple needed for the matching ``RemoveProfilePhoto`` write action.
    """
    result = await client(
        GetUserPhotosRequest(
            user_id=InputUserSelf(),
            offset=0,
            max_id=0,
            limit=action.limit,
        ),
    )
    raw_photos = [
        photo
        for photo in (getattr(result, "photos", []) or [])
        if int(getattr(photo, "id", 0) or 0)
    ]
    # Fetch thumbnails concurrently (serial awaits made the modal open scale
    # linearly with history size) but bounded — see ``_thumbs``.
    semaphore, flood_stop = thumb_limiter()
    items = await asyncio.gather(
        *(_profile_photo(client, photo, semaphore, flood_stop) for photo in raw_photos),
    )
    return TelegramProfilePhotos(items=list(items))


async def _profile_photo(
    client: TelegramClient,
    photo: object,
    semaphore: asyncio.Semaphore,
    flood_stop: asyncio.Event,
) -> TelegramProfilePhoto:
    return TelegramProfilePhoto(
        photo_id=int(getattr(photo, "id", 0) or 0),
        access_hash=int(getattr(photo, "access_hash", 0) or 0),
        file_reference=bytes(getattr(photo, "file_reference", b"") or b""),
        date_unix=_photo_date_unix(photo),
        thumb_bytes=await download_thumb_bounded(
            semaphore,
            flood_stop,
            "photos",
            lambda: _download_photo_thumb(client, photo),
        ),
    )


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
    crisp inside the 112 px poster card (and on retina). ``thumb=0``
    used to fetch the ~160 px stripped preview but stretching it 2x came
    out visibly pixelated.
    """
    try:
        # ``file=bytes`` (the type) is Telethon's in-memory download mode.
        data = await client.download_media(photo, file=bytes, thumb=-1)  # ty: ignore[invalid-argument-type]
    except errors.FloodWaitError:
        # Rate limits must reach the batch breaker in ``_thumbs`` — swallowing
        # them here let sibling downloads keep hammering a flooded connection.
        raise
    except (errors.RPCError, ValueError, TypeError):
        return None
    return data if isinstance(data, (bytes, bytearray)) else None
