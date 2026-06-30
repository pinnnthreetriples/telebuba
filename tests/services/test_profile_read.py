"""Tests for ``services.accounts.profile_read.fetch_live_account_profile``."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountProfileSnapshot
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
from services.accounts.profile_read import (
    account_profile_view,
    fetch_live_account_profile,
    invalidate_account_profile_cache,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    invalidate_account_profile_cache()
    yield
    invalidate_account_profile_cache()
    reset_logging_for_tests()


def _stub_execute_read_many(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[list[object]],
) -> None:
    async def fake_execute_read_many(
        _account_id: str,
        actions: list[object],
    ) -> list[object]:
        calls.append(list(actions))
        results: list[object] = []
        for action in actions:
            if isinstance(action, GetUserProfile):
                results.append(
                    TelegramProfileSnapshot(
                        first_name="Alice",
                        last_name="Liddell",
                        username="alice",
                        phone="79991234567",
                        bio="Hi there",
                        avatar_bytes=b"jpeg",
                    ),
                )
            elif isinstance(action, ListPinnedStories):
                results.append(
                    TelegramPinnedStories(
                        items=[
                            TelegramStoryThumb(
                                story_id=101,
                                kind="image",
                                caption="hi",
                                date_unix=1_600_000_000,
                                is_pinned=True,
                            ),
                        ],
                    ),
                )
            elif isinstance(action, ListActiveStories):
                results.append(
                    TelegramActiveStories(
                        items=[
                            TelegramStoryThumb(
                                story_id=202,
                                kind="video",
                                caption="active",
                                date_unix=1_700_000_000,
                                is_active=True,
                            ),
                        ],
                    ),
                )
            elif isinstance(action, ListProfileMusic):
                results.append(
                    TelegramProfileMusic(
                        items=[
                            TelegramMusicItem(file_id=555, title="Track", performer="Artist"),
                        ],
                        supported=True,
                    ),
                )
            elif isinstance(action, ListProfilePhotos):
                results.append(
                    TelegramProfilePhotos(
                        items=[
                            TelegramProfilePhoto(
                                photo_id=900,
                                access_hash=1,
                                file_reference=b"\x01",
                                date_unix=1_700_000_000,
                                thumb_bytes=b"thumb",
                            ),
                        ],
                    ),
                )
            else:
                msg = f"unexpected action {action}"
                raise TypeError(msg)
        return results

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read_many",
        fake_execute_read_many,
    )


@pytest.mark.asyncio
async def test_fetch_live_profile_returns_combined_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[object]] = []
    _stub_execute_read_many(monkeypatch, calls)

    snapshot = await fetch_live_account_profile("acc-1")

    assert isinstance(snapshot, AccountProfileSnapshot)
    assert snapshot.account_id == "acc-1"
    assert snapshot.first_name == "Alice"
    assert snapshot.bio == "Hi there"
    assert snapshot.avatar_bytes == b"jpeg"
    # Active first (newer date_unix), then pinned, deduped — pinned-only #101
    # and active-only #202 appear once each in date-descending order.
    assert [story.story_id for story in snapshot.stories] == [202, 101]
    assert snapshot.stories[0].is_active is True
    assert snapshot.stories[1].is_pinned is True
    assert [track.title for track in snapshot.music] == ["Track"]
    assert snapshot.music_supported is True
    assert [photo.photo_id for photo in snapshot.photos] == [900]
    assert snapshot.error is None
    assert snapshot.fetched_at_unix > 0

    # Single batch — regression guard: prior code did 3 parallel execute_read
    # calls and raced into "database is locked" under warming-runtime load.
    # Photo history and active-stories joined the batch later; the
    # one-session-per-fetch invariant must still hold for all five reads.
    assert len(calls) == 1, "fetch_live_account_profile must open ONE gateway session, not five"
    action_types = {type(action).__name__ for action in calls[0]}
    assert action_types == {
        "GetUserProfile",
        "ListPinnedStories",
        "ListActiveStories",
        "ListProfileMusic",
        "ListProfilePhotos",
    }


@pytest.mark.asyncio
async def test_fetch_live_profile_caches_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[object]] = []
    _stub_execute_read_many(monkeypatch, calls)

    snapshot_a = await fetch_live_account_profile("acc-1")
    snapshot_b = await fetch_live_account_profile("acc-1")

    assert snapshot_a is snapshot_b
    assert len(calls) == 1, "Second call inside TTL must hit cache, no extra gateway call"


@pytest.mark.asyncio
async def test_fetch_live_profile_force_refresh_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[object]] = []
    _stub_execute_read_many(monkeypatch, calls)

    await fetch_live_account_profile("acc-1")
    await fetch_live_account_profile("acc-1", force_refresh=True)

    assert len(calls) == 2, "force_refresh must trigger a fresh gateway round-trip"


@pytest.mark.asyncio
async def test_fetch_live_profile_ttl_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "read_snapshot_ttl_seconds", 1)
    calls: list[list[object]] = []
    _stub_execute_read_many(monkeypatch, calls)

    # Sequence: stamp call-1 snapshot @1000, _is_fresh check @1500 (past TTL),
    # stamp call-2 snapshot @1500.
    times = iter([1_000.0, 1_500.0, 1_500.0])
    monkeypatch.setattr("services.accounts.profile_read.time.time", lambda: next(times))

    await fetch_live_account_profile("acc-1")
    await fetch_live_account_profile("acc-1")

    assert len(calls) == 2, "Calls past TTL must re-fetch"


@pytest.mark.asyncio
async def test_fetch_live_profile_flood_wait_returns_error_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_read_many(_account_id: str, _actions: list[object]) -> list[object]:
        reason = "FloodWait(42s)"
        raise TelegramReadError(reason)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read_many",
        fake_execute_read_many,
    )

    snapshot = await fetch_live_account_profile("acc-flood")

    assert isinstance(snapshot, AccountProfileSnapshot)
    assert snapshot.account_id == "acc-flood"
    assert snapshot.error is not None
    assert "FloodWait" in snapshot.error
    assert snapshot.first_name is None


@pytest.mark.asyncio
async def test_fetch_live_profile_does_not_cache_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed fetch must NOT be cached — reopening the dialog should retry.

    Otherwise a transient FloodWait/RPC error pins the dialog to a stale error
    for the whole TTL even on a plain (force_refresh=False) reopen.
    """
    calls: list[int] = []

    async def fake_execute_read_many(_account_id: str, _actions: list[object]) -> list[object]:
        calls.append(1)
        reason = "FloodWait(7s)"
        raise TelegramReadError(reason)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read_many",
        fake_execute_read_many,
    )

    first = await fetch_live_account_profile("acc-err")
    second = await fetch_live_account_profile("acc-err")

    assert first.error is not None
    assert second.error is not None
    assert len(calls) == 2, "error snapshots must not be cached — second open re-fetches"


@pytest.mark.asyncio
async def test_fetch_live_profile_unexpected_error_returns_error_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_read_many(_account_id: str, _actions: list[object]) -> list[object]:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read_many",
        fake_execute_read_many,
    )

    snapshot = await fetch_live_account_profile("acc-broken")

    assert snapshot.error is not None
    assert "RuntimeError" in snapshot.error
    assert snapshot.first_name is None


@pytest.mark.asyncio
async def test_invalidate_cache_drops_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[object]] = []
    _stub_execute_read_many(monkeypatch, calls)

    await fetch_live_account_profile("acc-1")
    invalidate_account_profile_cache("acc-1")
    await fetch_live_account_profile("acc-1")

    assert len(calls) == 2, "Invalidated cache must trigger a fresh fetch"


@pytest.mark.asyncio
async def test_account_profile_view_encodes_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return AccountProfileSnapshot(
            account_id=account_id,
            avatar_bytes=b"\xff\xd8avatar",
            photos=[
                TelegramProfilePhoto(
                    photo_id=1, access_hash=2, file_reference=b"ref", thumb_bytes=b"\x89PNG"
                ),
            ],
            stories=[TelegramStoryThumb(story_id=3, kind="image", privacy_preset="contacts")],
            music=[TelegramMusicItem(file_id=4, title="T", access_hash=5, file_reference=b"mref")],
            music_supported=True,
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1")

    assert view.error is None
    assert view.avatar_data_uri is not None
    assert view.avatar_data_uri.startswith("data:image/jpeg;base64,")
    assert view.photos[0].file_reference == base64.b64encode(b"ref").decode()
    assert view.photos[0].thumb_data_uri is not None
    assert view.stories[0].story_id == 3
    assert view.music[0].file_reference == base64.b64encode(b"mref").decode()


@pytest.mark.asyncio
async def test_account_profile_view_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return AccountProfileSnapshot(account_id=account_id, error="floodwait")

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1")

    assert view.error == "floodwait"
    assert view.photos == []
    assert view.music == []
