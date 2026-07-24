"""Tests for ``services.accounts.profile_read.fetch_live_account_profile``."""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
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
    account_avatar_image,
    account_profile_image,
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
        return _fake_read_results(actions)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read_many",
        fake_execute_read_many,
    )


def _fake_read_results(actions: list[object]) -> list[object]:
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
                        TelegramStoryThumb(
                            story_id=202,
                            caption="stale",
                            date_unix=1_650_000_000,
                            is_pinned=True,
                            views=1,
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
    # Active first (newer date_unix), then pinned, deduped — pinned-only #101
    # and active-only #202 appear once each in date-descending order.
    assert [story.story_id for story in snapshot.stories] == [202, 101]
    assert snapshot.stories[0].is_active is True
    assert snapshot.stories[0].is_pinned is True
    assert snapshot.stories[0].caption == "active"
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
    action_types = [type(action).__name__ for action in calls[0]]
    assert action_types == [
        "GetUserProfile",
        "ListPinnedStories",
        "ListActiveStories",
        "ListProfileMusic",
        "ListProfilePhotos",
    ]


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
async def test_fetch_live_profile_missing_account_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def missing(_account_id: str, _actions: list[object]) -> list[object]:
        nonlocal calls
        calls += 1
        message = "missing-account"
        raise TelegramAccountNotFoundError(message)

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", missing)

    first = await fetch_live_account_profile("acc-missing")
    second = await fetch_live_account_profile("acc-missing")

    assert first.error == second.error == "missing-account"
    assert calls == 2, "missing-account snapshots must not poison the TTL cache"


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
    events: list[tuple[str, str, str | None, dict[str, object] | None]] = []

    async def fake_execute_read_many(_account_id: str, _actions: list[object]) -> list[object]:
        msg = "boom"
        raise RuntimeError(msg)

    async def log(
        level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, account_id, extra))

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read_many",
        fake_execute_read_many,
    )
    monkeypatch.setattr("services.accounts.profile_read.log_event", log)

    snapshot = await fetch_live_account_profile("acc-broken")

    assert snapshot.error == "RuntimeError: boom"
    assert snapshot.first_name is None
    assert events == [
        (
            "ERROR",
            "account_profile_read_failed_unexpected",
            "acc-broken",
            {"error_type": "RuntimeError", "error": "boom"},
        ),
    ]


@pytest.mark.asyncio
async def test_invalidate_cache_drops_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[object]] = []
    _stub_execute_read_many(monkeypatch, calls)

    await fetch_live_account_profile("acc-1")
    invalidate_account_profile_cache("acc-1")
    await fetch_live_account_profile("acc-1")

    assert len(calls) == 2, "Invalidated cache must trigger a fresh fetch"


def _gated_execute_read_many(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[int],
    started: asyncio.Event,
    release: asyncio.Event,
) -> None:
    """Stub whose fetch blocks on ``release`` — lets tests order events mid-flight."""

    async def gated(_account_id: str, actions: list[object]) -> list[object]:
        calls.append(1)
        started.set()
        await asyncio.wait_for(release.wait(), timeout=2.0)
        return _fake_read_results(actions)

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", gated)


@pytest.mark.asyncio
async def test_invalidate_mid_flight_prevents_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch that started before a mutation must not repopulate the cache."""
    calls: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()
    _gated_execute_read_many(monkeypatch, calls, started, release)

    task = asyncio.create_task(fetch_live_account_profile("acc-race"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        invalidate_account_profile_cache("acc-race")
        release.set()
        snapshot = await asyncio.wait_for(task, timeout=2.0)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert snapshot.error is None

    await fetch_live_account_profile("acc-race")
    assert len(calls) == 2, "stale in-flight snapshot must not survive invalidation"


@pytest.mark.asyncio
async def test_global_invalidate_mid_flight_prevents_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The account_id=None (clear-everything) form must also stop mid-flight stores."""
    calls: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()
    _gated_execute_read_many(monkeypatch, calls, started, release)

    task = asyncio.create_task(fetch_live_account_profile("acc-race-all"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        invalidate_account_profile_cache()
        release.set()
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    await fetch_live_account_profile("acc-race-all")
    assert len(calls) == 2, "global invalidation must not be overwritten mid-flight"


@pytest.mark.asyncio
async def test_concurrent_fetches_share_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent cold fetches coalesce into one gateway round-trip."""
    calls: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()
    _gated_execute_read_many(monkeypatch, calls, started, release)

    tasks = [asyncio.create_task(fetch_live_account_profile("acc-flight")) for _ in range(3)]
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await asyncio.sleep(0)
        release.set()
        snapshots = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert len(calls) == 1, "concurrent callers must share the in-flight fetch"
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


@pytest.mark.asyncio
async def test_account_profile_view_encodes_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:
        calls.append((account_id, force_refresh))
        return AccountProfileSnapshot(
            account_id=account_id,
            first_name="",
            last_name=None,
            username="u",
            bio="",
            current_photo_id=9_007_199_254_740_993,
            photos=[
                TelegramProfilePhoto(
                    photo_id=9_007_199_254_740_993,
                    access_hash=-9_007_199_254_740_993,
                    file_reference=b"ref",
                    thumb_bytes=b"\x89PNG",
                ),
            ],
            stories=[
                TelegramStoryThumb(
                    story_id=0,
                    kind="image",
                    caption="Rabbit hole",
                    privacy_preset="contacts",
                    thumb_bytes=b"\x89story",
                    is_pinned=True,
                    views=0,
                    reactions=0,
                ),
            ],
            music=[
                TelegramMusicItem(
                    file_id=9_007_199_254_740_993,
                    title="T",
                    performer="Artist",
                    access_hash=-9_007_199_254_740_993,
                    file_reference=b"mref",
                ),
            ],
            music_supported=False,
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1", force_refresh=True)

    assert calls == [("acc-1", True)]
    assert view.error is None
    assert (view.first_name, view.last_name, view.username, view.bio) == ("", None, "u", "")
    assert view.photos[0].file_reference == base64.b64encode(b"ref").decode()
    assert (
        view.photos[0].thumb_url
        == f"/api/{settings.api.version}/accounts/acc-1/profile/photos/9007199254740993/thumb"
    )
    assert (view.photos[0].photo_id, view.photos[0].access_hash) == (
        "9007199254740993",
        "-9007199254740993",
    )
    assert view.stories[0].story_id == 0
    assert (view.stories[0].kind, view.stories[0].caption, view.stories[0].privacy_preset) == (
        "image",
        "Rabbit hole",
        "contacts",
    )
    assert (
        view.stories[0].thumb_url
        == f"/api/{settings.api.version}/accounts/acc-1/profile/stories/0/thumb"
    )
    assert view.stories[0].is_pinned is True
    assert (view.stories[0].views, view.stories[0].reactions) == (0, 0)
    assert (view.music[0].file_id, view.music[0].access_hash) == (
        "9007199254740993",
        "-9007199254740993",
    )
    assert (view.music[0].title, view.music[0].performer) == ("T", "Artist")
    assert view.music[0].file_reference == base64.b64encode(b"mref").decode()
    assert view.music_supported is False


@pytest.mark.asyncio
async def test_account_profile_view_marks_exactly_one_main_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The view flags is_main on the photo whose id matches current_photo_id — only it.

    The current avatar need NOT be index 0 (Telegram's history order and the
    real current-avatar are independent), so the flag is matched by identity.
    """

    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return AccountProfileSnapshot(
            account_id=account_id,
            current_photo_id=2,
            photos=[
                TelegramProfilePhoto(photo_id=1, access_hash=1, file_reference=b"a"),
                TelegramProfilePhoto(photo_id=2, access_hash=2, file_reference=b"b"),
                TelegramProfilePhoto(photo_id=3, access_hash=3, file_reference=b"c"),
            ],
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1")

    assert [photo.is_main for photo in view.photos] == [False, True, False]
    assert sum(photo.is_main for photo in view.photos) == 1
    # Ordering is preserved (not reordered so main is first).
    assert [photo.photo_id for photo in view.photos] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_account_profile_view_no_main_when_current_id_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No photo is flagged main when the account has no current avatar id."""

    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return AccountProfileSnapshot(
            account_id=account_id,
            current_photo_id=None,
            photos=[TelegramProfilePhoto(photo_id=1, access_hash=1, file_reference=b"a")],
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1")

    assert [photo.is_main for photo in view.photos] == [False]


@pytest.mark.asyncio
async def test_account_profile_view_thumb_url_none_without_thumb_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A photo/story with no cached thumbnail gets no thumb_url — nothing to serve."""

    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return AccountProfileSnapshot(
            account_id=account_id,
            photos=[TelegramProfilePhoto(photo_id=1, access_hash=2, file_reference=b"ref")],
            stories=[TelegramStoryThumb(story_id=3, kind="image", privacy_preset="contacts")],
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1")

    assert view.photos[0].thumb_url is None
    assert view.stories[0].thumb_url is None


@pytest.mark.asyncio
async def test_account_profile_view_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return AccountProfileSnapshot(account_id=account_id, error="floodwait")

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)
    view = await account_profile_view("acc-1")

    assert view.error == "floodwait"
    assert view.photos == []
    assert view.music == []


def _image_snapshot() -> AccountProfileSnapshot:
    return AccountProfileSnapshot(
        account_id="acc-1",
        photos=[
            TelegramProfilePhoto(
                photo_id=1, access_hash=2, file_reference=b"ref", thumb_bytes=b"photo-thumb"
            ),
            TelegramProfilePhoto(photo_id=2, access_hash=3, file_reference=b"ref2"),
        ],
        stories=[
            TelegramStoryThumb(
                story_id=9, kind="image", privacy_preset="contacts", thumb_bytes=b"story-thumb"
            ),
        ],
    )


@pytest.mark.asyncio
async def test_account_profile_image_returns_photo_bytes_and_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        requested.append(account_id)
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)

    image = await account_profile_image("acc-1", kind="photos", item_id=1)

    assert image is not None
    assert requested == ["acc-1"]
    assert image.content == b"photo-thumb"
    assert image.media_type == "image/jpeg"
    assert image.etag  # non-empty content hash


@pytest.mark.asyncio
async def test_account_profile_image_returns_story_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)

    image = await account_profile_image("acc-1", kind="stories", item_id=9)

    assert image is not None
    assert image.content == b"story-thumb"


@pytest.mark.asyncio
async def test_account_profile_image_unknown_id_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)

    assert await account_profile_image("acc-1", kind="photos", item_id=999) is None
    assert await account_profile_image("acc-1", kind="stories", item_id=999) is None


@pytest.mark.asyncio
async def test_account_profile_image_no_thumb_bytes_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Photo #2 exists in the snapshot but has no cached thumb — 404, not empty bytes."""

    async def _snap(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:  # noqa: ARG001
        return _image_snapshot()

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", _snap)

    assert await account_profile_image("acc-1", kind="photos", item_id=2) is None


@pytest.mark.asyncio
async def test_account_avatar_image_wraps_db_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(account_id: str) -> tuple[bytes, str] | None:
        assert account_id == "acc-1"
        return b"avatar-bytes", "etag-xyz"

    monkeypatch.setattr("services.accounts.profile_read.fetch_account_avatar", _fake)
    image = await account_avatar_image("acc-1")
    assert image is not None
    assert image.content == b"avatar-bytes"
    assert image.etag == "etag-xyz"


@pytest.mark.asyncio
async def test_account_avatar_image_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(account_id: str) -> tuple[bytes, str] | None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.accounts.profile_read.fetch_account_avatar", _fake)
    assert await account_avatar_image("acc-1") is None
