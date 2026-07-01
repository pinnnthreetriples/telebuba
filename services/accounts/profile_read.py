"""Live-profile reads for the edit-profile dialog.

Calls the three read actions on the Telegram gateway inside ONE Telethon
session, caches the combined snapshot in-process for
``profile_media.read_snapshot_ttl_seconds``, and degrades gracefully when
Telegram refuses the fetch (FloodWait, RPCError, missing account) — the dialog
still opens and shows whatever it can.

The gateway is imported at module scope so tests monkeypatch
``services.accounts.profile_read.execute_read_many`` rather than reaching into
the gateway internals.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, cast

from core.config import settings
from core.logging import log_event
from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read_many,
)
from schemas.accounts import AccountProfileSnapshot
from schemas.profile_media import (
    AccountProfileView,
    ProfileMusicView,
    ProfilePhotoView,
    ProfileStoryView,
)
from schemas.telegram_actions import (
    GetUserProfile,
    ListActiveStories,
    ListPinnedStories,
    ListProfileMusic,
    ListProfilePhotos,
)

if TYPE_CHECKING:
    from schemas.telegram_profile_snapshot import (
        TelegramActiveStories,
        TelegramPinnedStories,
        TelegramProfileMusic,
        TelegramProfilePhotos,
        TelegramProfileSnapshot,
        TelegramStoryThumb,
    )

__all__ = [
    "account_profile_view",
    "fetch_live_account_profile",
    "invalidate_account_profile_cache",
]


def _data_uri(data: bytes | None, mime: str = "image/jpeg") -> str | None:
    """Encode raw image bytes as a ``data:`` URI for the JSON profile view."""
    if not data:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def account_profile_view(
    account_id: str,
    *,
    force_refresh: bool = False,
) -> AccountProfileView:
    """JSON-safe live profile for the edit-profile modal (the SPA reads this)."""
    snapshot = await fetch_live_account_profile(account_id, force_refresh=force_refresh)
    if snapshot.error is not None:
        return AccountProfileView(error=snapshot.error)
    return AccountProfileView(
        first_name=snapshot.first_name,
        last_name=snapshot.last_name,
        username=snapshot.username,
        bio=snapshot.bio,
        avatar_data_uri=_data_uri(snapshot.avatar_bytes),
        photos=[
            ProfilePhotoView(
                photo_id=photo.photo_id,
                access_hash=photo.access_hash,
                file_reference=base64.b64encode(photo.file_reference).decode("ascii"),
                thumb_data_uri=_data_uri(photo.thumb_bytes),
            )
            for photo in snapshot.photos
        ],
        stories=[
            ProfileStoryView(
                story_id=story.story_id,
                kind=story.kind,
                caption=story.caption,
                privacy_preset=story.privacy_preset,
                is_pinned=story.is_pinned,
                thumb_data_uri=_data_uri(story.thumb_bytes),
            )
            for story in snapshot.stories
        ],
        music=[
            ProfileMusicView(
                file_id=track.file_id,
                title=track.title,
                performer=track.performer,
                access_hash=track.access_hash,
                file_reference=base64.b64encode(track.file_reference).decode("ascii"),
            )
            for track in snapshot.music
        ],
        music_supported=snapshot.music_supported,
    )


_CACHE: dict[str, AccountProfileSnapshot] = {}


def _is_fresh(snapshot: AccountProfileSnapshot) -> bool:
    ttl = settings.profile_media.read_snapshot_ttl_seconds
    return (time.time() - snapshot.fetched_at_unix) < ttl


async def fetch_live_account_profile(
    account_id: str,
    *,
    force_refresh: bool = False,
) -> AccountProfileSnapshot:
    """Return the live Telegram profile snapshot for ``account_id``.

    Uses an in-process TTL cache to keep repeated dialog opens cheap.
    ``force_refresh=True`` bypasses the cache (the "↻" button in the dialog).
    On Telegram refusal returns an :class:`AccountProfileSnapshot` whose
    ``error`` field carries the reason — the caller renders the dialog
    anyway, showing whatever fields are still populated.
    """
    cached = _CACHE.get(account_id)
    if cached is not None and not force_refresh and _is_fresh(cached):
        return cached

    snapshot = await _fetch_live_or_error(account_id)
    # Don't cache failures: a transient FloodWait/RPC/network error would
    # otherwise pin the dialog to a stale error for the whole TTL, so reopening
    # (force_refresh=False) would keep showing it instead of retrying.
    if snapshot.error is None:
        _CACHE[account_id] = snapshot
    return snapshot


def invalidate_account_profile_cache(account_id: str | None = None) -> None:
    """Drop cached snapshots — ``None`` clears the entire cache.

    Called after a profile edit so the next dialog open reflects the new state
    immediately instead of waiting for the TTL.
    """
    if account_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(account_id, None)


async def _fetch_live_or_error(account_id: str) -> AccountProfileSnapshot:
    try:
        results = await execute_read_many(
            account_id,
            [
                GetUserProfile(),
                ListPinnedStories(),
                ListActiveStories(),
                ListProfileMusic(),
                ListProfilePhotos(),
            ],
        )
    except TelegramReadError as exc:
        return _error_snapshot(account_id, exc.reason)
    except TelegramAccountNotFoundError as exc:
        return _error_snapshot(account_id, str(exc))
    except Exception as exc:  # noqa: BLE001 — last-resort: dialog must still open
        await log_event(
            "ERROR",
            "account_profile_read_failed_unexpected",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return _error_snapshot(account_id, f"{type(exc).__name__}: {exc}")

    # The gateway returns the snapshot types matching each action's position.
    # ``cast`` documents the contract for type checkers without paying for a
    # runtime isinstance check on the happy path.
    profile_model, pinned_model, active_model, music_model, photos_model = results
    profile = cast("TelegramProfileSnapshot", profile_model)
    music = cast("TelegramProfileMusic", music_model)
    return AccountProfileSnapshot(
        account_id=account_id,
        **profile.model_dump(),
        stories=_merge_stories(
            cast("TelegramPinnedStories", pinned_model).items,
            cast("TelegramActiveStories", active_model).items,
        ),
        music=music.items,
        music_supported=music.supported,
        photos=cast("TelegramProfilePhotos", photos_model).items,
        fetched_at_unix=time.time(),
    )


def _error_snapshot(account_id: str, error: str) -> AccountProfileSnapshot:
    return AccountProfileSnapshot(
        account_id=account_id,
        fetched_at_unix=time.time(),
        error=error,
    )


def _merge_stories(
    pinned: list[TelegramStoryThumb],
    active: list[TelegramStoryThumb],
) -> list[TelegramStoryThumb]:
    """Dedupe active + pinned by story_id, newest-first by date_unix.

    A story can sit in both lists when it's pinned to the profile AND still
    inside its 24 h active window. Active entries win the merge because they
    carry fresher view-count data; the pinned flag is grafted onto the active
    record so the UI badge still reads correctly.
    """
    by_id: dict[int, TelegramStoryThumb] = {item.story_id: item for item in active}
    for item in pinned:
        existing = by_id.get(item.story_id)
        if existing is None:
            by_id[item.story_id] = item
        else:
            by_id[item.story_id] = existing.model_copy(update={"is_pinned": True})
    return sorted(by_id.values(), key=lambda story: story.date_unix, reverse=True)
