"""Live-profile reads for the edit-profile dialog.

Calls the three read actions on the Telegram gateway in parallel, caches the
combined snapshot in-process for ``profile_media.read_snapshot_ttl_seconds``,
and degrades gracefully when Telegram refuses the fetch (FloodWait, RPCError,
missing account) — the dialog still opens and shows whatever it can.

The gateway is imported at module scope so tests monkeypatch
``services.accounts.profile_read.execute_read`` rather than reaching into the
gateway internals.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, cast

from core.config import settings
from core.logging import log_event
from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
)
from schemas.accounts import AccountProfileSnapshot
from schemas.telegram_actions import (
    GetUserProfile,
    ListPinnedStories,
    ListProfileMusic,
)

if TYPE_CHECKING:
    from schemas.telegram_profile_snapshot import (
        TelegramPinnedStories,
        TelegramProfileMusic,
        TelegramProfileSnapshot,
    )

__all__ = ["fetch_live_account_profile", "invalidate_account_profile_cache"]


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
        profile_model, stories_model, music_model = await asyncio.gather(
            execute_read(account_id, GetUserProfile()),
            execute_read(account_id, ListPinnedStories()),
            execute_read(account_id, ListProfileMusic()),
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

    # The gateway returns the snapshot type matching each action. ``cast``
    # documents the contract for type checkers without paying for a runtime
    # isinstance check on the happy path.
    return _combine(
        account_id,
        cast("TelegramProfileSnapshot", profile_model),
        cast("TelegramPinnedStories", stories_model),
        cast("TelegramProfileMusic", music_model),
    )


def _error_snapshot(account_id: str, error: str) -> AccountProfileSnapshot:
    return AccountProfileSnapshot(
        account_id=account_id,
        fetched_at_unix=time.time(),
        error=error,
    )


def _combine(
    account_id: str,
    profile: TelegramProfileSnapshot,
    stories: TelegramPinnedStories,
    music: TelegramProfileMusic,
) -> AccountProfileSnapshot:
    return AccountProfileSnapshot(
        account_id=account_id,
        **profile.model_dump(),
        stories=stories.items,
        music=music.items,
        music_supported=music.supported,
        fetched_at_unix=time.time(),
    )
