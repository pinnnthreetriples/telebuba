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
from telethon.tl.functions.stories import GetPinnedStoriesRequest
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
from core.telegram_client._client import telegram_client
from schemas.device_fingerprint import TelegramClientRequest
from schemas.telegram_actions import (
    GetUserProfile,
    ListPinnedStories,
    ListProfileMusic,
)
from schemas.telegram_profile_snapshot import (
    TelegramMusicItem,
    TelegramPinnedStories,
    TelegramProfileMusic,
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
    """Dispatch multiple read actions inside ONE Telethon connection.

    The dialog needs three reads (profile + stories + music) per open. Running
    them as three parallel ``execute_read`` calls opens three Telethon clients
    on the same ``.session`` SQLite file and three concurrent ``fetch_account``
    reads on ``telebuba.db`` — under live warming-runtime load that races into
    ``OperationalError: database is locked``. Batching through one client +
    one ``fetch_account`` removes both contention sources.

    Actions execute sequentially inside the open session, in input order.
    Telethon errors (FloodWait, RPC, etc.) are caught and re-raised as
    :class:`TelegramReadError` so the service layer can handle them without
    importing telethon (layer isolation, non-negotiable #5).
    """
    account = await fetch_account(account_id)
    if account is None:
        msg = f"Account not found: {account_id}"
        raise TelegramAccountNotFoundError(msg)

    request = TelegramClientRequest(account_id=account_id, session_name=account.session_name)
    async with telegram_client(request) as client:
        try:
            await client.connect()
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
        case ListProfileMusic():
            return await _dispatch_list_profile_music(client)
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
    items: list[TelegramStoryThumb] = []
    for story in raw_stories:
        story_id = int(getattr(story, "id", 0) or 0)
        if story_id == 0:
            continue
        items.append(
            TelegramStoryThumb(
                story_id=story_id,
                kind=_story_kind(story),
                caption=_optional_str(getattr(story, "caption", None)),
                thumb_bytes=await _download_story_thumb(client, story),
            ),
        )
    return TelegramPinnedStories(items=items)


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
    media = getattr(story, "media", None)
    if media is None:
        return None
    try:
        # ``file=bytes`` (the type) is Telethon's in-memory mode; the stub
        # under-specifies the union so ty needs the override here.
        data = await client.download_media(media, file=bytes, thumb=0)  # ty: ignore[invalid-argument-type]
    except (errors.RPCError, ValueError, TypeError):
        # ``thumb=0`` fails on some media kinds; the UI can show a placeholder
        # instead of crashing the whole dialog open.
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
            ),
        )
    return TelegramProfileMusic(items=items, supported=True)


def _find_audio_attribute(document: object) -> object | None:
    for attribute in getattr(document, "attributes", []) or []:
        if isinstance(attribute, DocumentAttributeAudio):
            return attribute
    return None
