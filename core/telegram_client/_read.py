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

import asyncio
from typing import TYPE_CHECKING, cast

from telethon import errors
from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantRequest
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    ChannelParticipantBanned,
    DocumentAttributeAudio,
    InputUserSelf,
)

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.telegram_client._pool import TelegramClientPoolError, get_client
from core.telegram_client._read_challenge import dispatch_wait_for_bot_challenge
from core.telegram_client._read_stories import (
    dispatch_list_active_stories,
    dispatch_list_pinned_stories,
)
from core.telegram_client._thumbs import download_thumb_bounded, thumb_limiter
from schemas.telegram_actions import (
    BanCheckResult,
    CheckBannedInChannel,
    CheckMessagesAlive,
    CheckMessagesAliveResult,
    GetLinkedDiscussionGroup,
    GetUserProfile,
    LinkedDiscussionGroupResult,
    ListActiveStories,
    ListPinnedStories,
    ListProfileMusic,
    ListProfilePhotos,
    WaitForBotChallenge,
)
from schemas.telegram_profile_snapshot import (
    TelegramMusicItem,
    TelegramProfileMusic,
    TelegramProfilePhoto,
    TelegramProfilePhotos,
    TelegramProfileSnapshot,
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
    except (TelegramClientPoolError, ConnectionError, TimeoutError) as exc:
        # Pool/socket failures must not leak raw past the gateway — services
        # only handle ``TelegramReadError`` (layer contract, non-negotiable #6).
        reason = f"{type(exc).__name__}: {exc}"
        raise TelegramReadError(reason) from exc
    else:
        return results


async def _dispatch_read_action(  # noqa: PLR0911 - one return per read-action case
    client: TelegramClient,
    action: TelegramReadAction,
) -> BaseModel:
    match action:
        case GetLinkedDiscussionGroup():
            return await _dispatch_get_linked_group(client, action)
        case CheckMessagesAlive():
            return await _dispatch_check_messages_alive(client, action)
        case CheckBannedInChannel():
            return await _dispatch_check_banned(client, action)
        case WaitForBotChallenge():
            return await dispatch_wait_for_bot_challenge(client, action)
        case GetUserProfile():
            return await _dispatch_get_user_profile(client)
        case ListPinnedStories():
            return await dispatch_list_pinned_stories(client, action)
        case ListActiveStories():
            return await dispatch_list_active_stories(client)
        case ListProfileMusic():
            return await _dispatch_list_profile_music(client)
        case ListProfilePhotos():
            return await _dispatch_list_profile_photos(client, action)
        case _:  # pragma: no cover - discriminated union is exhaustive
            msg = f"Unsupported read action_type: {action.action_type}"
            raise ValueError(msg)


async def _dispatch_get_linked_group(
    client: TelegramClient,
    action: GetLinkedDiscussionGroup,
) -> LinkedDiscussionGroupResult:
    """Resolve a channel's linked discussion group via ``GetFullChannelRequest``.

    ``full_chat.linked_chat_id`` is ``None`` when the channel has comments
    disabled or has no linked group.
    """
    result = await client(GetFullChannelRequest(channel=action.channel))  # ty: ignore[invalid-argument-type]
    linked = getattr(getattr(result, "full_chat", None), "linked_chat_id", None)
    linked_id = int(linked) if linked is not None else None
    return LinkedDiscussionGroupResult(
        linked_chat_id=linked_id,
        comments_enabled=linked_id is not None,
    )


async def _resolve_linked_group_entity(client: TelegramClient, channel: str) -> object | None:
    """Resolve ``channel``'s linked discussion-group entity, or ``None`` if there is none.

    The ban and deletion probes both act on the linked group — comments live there,
    not on the broadcast channel. ``GetFullChannelRequest`` carries the bare
    ``linked_chat_id`` and *usually* the resolved ``Channel`` in ``chats`` (with
    access_hash), but Telegram omits that entity for some channels, so we fall back to
    ``get_input_entity`` off the warm session cache (the account joined the group at
    onboarding — same idiom as ``_read_challenge``). ``None`` means no linked group /
    comments disabled / the id couldn't be resolved.
    """
    full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    linked = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
    if linked is None:
        return None
    linked_id = int(linked)
    entity = next(
        (chat for chat in getattr(full, "chats", []) if int(getattr(chat, "id", 0)) == linked_id),
        None,
    )
    if entity is not None:
        return entity
    try:
        return await client.get_input_entity(linked_id)
    except (ValueError, TypeError, errors.RPCError):
        return None


async def _dispatch_check_messages_alive(
    client: TelegramClient,
    action: CheckMessagesAlive,
) -> CheckMessagesAliveResult:
    """Re-read ``message_ids`` in ``channel``'s linked discussion group; ``None`` → gone.

    Comments are posted via ``comment_to``, so they live in the channel's linked
    discussion group. ``get_messages`` yields ``None`` for a message that was deleted
    or is no longer visible. Group unresolvable → we cannot verify, report nothing missing.
    """
    entity = await _resolve_linked_group_entity(client, action.channel)
    if entity is None:
        return CheckMessagesAliveResult(missing_ids=[])
    # get_messages(ids=[...]) returns a list aligned to ids (None where a message is
    # gone); the stub's union also admits the single-id Message form we never use here.
    messages = cast(
        "list[object | None]", await client.get_messages(entity, ids=action.message_ids)
    )
    missing = [
        mid for mid, message in zip(action.message_ids, messages, strict=True) if message is None
    ]
    return CheckMessagesAliveResult(missing_ids=missing)


def _classify_participant(participant: object) -> BanCheckResult:
    """Map a resolved participant object to a ban-check state.

    ``ChannelParticipantBanned`` with ``view_messages`` restricted = kicked out
    (treated as ``not_member``); with only ``send_messages`` restricted = muted
    (``restricted``). Any other participant type is a normal/admin member able to
    comment (``can_send``).
    """
    if isinstance(participant, ChannelParticipantBanned):
        rights = getattr(participant, "banned_rights", None)
        if getattr(rights, "view_messages", False):
            return BanCheckResult(state="not_member")
        if getattr(rights, "send_messages", False):
            return BanCheckResult(state="restricted")
    return BanCheckResult(state="can_send")


async def _dispatch_check_banned(
    client: TelegramClient,
    action: CheckBannedInChannel,
) -> BanCheckResult:
    """Probe whether the account is banned/write-forbidden in ``channel``.

    Resolve the linked discussion group (the ban lives in the group, not the
    broadcast channel), then read the account's own participant state via
    ``GetParticipantRequest`` — a pure read, no message is sent. Not a participant →
    kicked/never-joined (``not_member``); no linked group / comments off / group
    unresolvable → ``comments_disabled``.
    """
    entity = await _resolve_linked_group_entity(client, action.channel)
    if entity is None:
        return BanCheckResult(state="comments_disabled")
    try:
        result = await client(GetParticipantRequest(channel=entity, participant=InputUserSelf()))  # ty: ignore[invalid-argument-type]
    except errors.UserNotParticipantError:
        return BanCheckResult(state="not_member")
    return _classify_participant(getattr(result, "participant", None))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _dispatch_get_user_profile(client: TelegramClient) -> TelegramProfileSnapshot:
    full = await client(GetFullUserRequest(InputUserSelf()))
    full_user = getattr(full, "full_user", None)
    bio = _optional_str(getattr(full_user, "about", None))
    # ``UserFull.profile_photo`` is the current avatar; its id is authoritative
    # for marking which history photo is "main" (reused read — no extra request).
    raw_photo_id = getattr(getattr(full_user, "profile_photo", None), "id", None)
    current_photo_id = raw_photo_id if isinstance(raw_photo_id, int) else None
    users = getattr(full, "users", []) or []
    user = users[0] if users else None
    return TelegramProfileSnapshot(
        first_name=_optional_str(getattr(user, "first_name", None)),
        last_name=_optional_str(getattr(user, "last_name", None)),
        username=_optional_str(getattr(user, "username", None)),
        phone=_optional_str(getattr(user, "phone", None)),
        bio=bio,
        current_photo_id=current_photo_id,
    )


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
