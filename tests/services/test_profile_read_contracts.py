"""Read-side mapping contracts for account profile snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.telegram_client import TelegramAccountNotFoundError
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


@pytest.fixture(autouse=True)
def _clear_profile_cache() -> Iterator[None]:
    invalidate_account_profile_cache()
    yield
    invalidate_account_profile_cache()


@pytest.mark.asyncio
async def test_gateway_batch_order_matches_positional_result_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[type[object]] = []

    async def execute(_account_id: str, actions: list[object]) -> list[object]:
        seen.extend(type(action) for action in actions)
        return [
            TelegramProfileSnapshot(first_name="Ada"),
            TelegramPinnedStories(),
            TelegramActiveStories(),
            TelegramProfileMusic(supported=False),
            TelegramProfilePhotos(),
        ]

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)

    snapshot = await fetch_live_account_profile("acc-order")

    assert seen == [
        GetUserProfile,
        ListPinnedStories,
        ListActiveStories,
        ListProfileMusic,
        ListProfilePhotos,
    ]
    assert snapshot.first_name == "Ada"
    assert snapshot.music_supported is False


@pytest.mark.asyncio
async def test_story_merge_keeps_fresh_active_data_and_grafts_pinned_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(_account_id: str, _actions: list[object]) -> list[object]:
        return [
            TelegramProfileSnapshot(),
            TelegramPinnedStories(
                items=[
                    TelegramStoryThumb(
                        story_id=7,
                        caption="stale",
                        date_unix=10,
                        is_pinned=True,
                        views=1,
                    ),
                    TelegramStoryThumb(story_id=3, date_unix=30, is_pinned=True),
                ],
            ),
            TelegramActiveStories(
                items=[
                    TelegramStoryThumb(
                        story_id=7,
                        caption="fresh",
                        date_unix=20,
                        is_active=True,
                        views=99,
                    ),
                ],
            ),
            TelegramProfileMusic(),
            TelegramProfilePhotos(),
        ]

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)

    snapshot = await fetch_live_account_profile("acc-merge")

    assert [story.story_id for story in snapshot.stories] == [3, 7]
    merged = snapshot.stories[1]
    assert (merged.caption, merged.views, merged.is_active, merged.is_pinned) == (
        "fresh",
        99,
        True,
        True,
    )


@pytest.mark.asyncio
async def test_account_not_found_is_a_retryable_error_snapshot_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def execute(_account_id: str, _actions: list[object]) -> list[object]:
        nonlocal calls
        calls += 1
        message = "missing-account"
        raise TelegramAccountNotFoundError(message)

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)

    first = await fetch_live_account_profile("acc-missing")
    second = await fetch_live_account_profile("acc-missing")

    assert first.account_id == "acc-missing"
    assert first.error == "missing-account"
    assert second.error == "missing-account"
    assert calls == 2, "error snapshots must not poison the TTL cache"


@pytest.mark.asyncio
async def test_profile_view_forwards_refresh_and_maps_all_boundary_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    async def snapshot(account_id: str, *, force_refresh: bool = False) -> AccountProfileSnapshot:
        calls.append((account_id, force_refresh))
        return AccountProfileSnapshot(
            account_id=account_id,
            first_name="",
            last_name=None,
            username="u",
            bio="",
            current_photo_id=2,
            photos=[
                TelegramProfilePhoto(
                    photo_id=2,
                    access_hash=-9,
                    file_reference=b"\x00\xff",
                    thumb_bytes=b"thumb",
                ),
            ],
            stories=[
                TelegramStoryThumb(
                    story_id=0,
                    views=0,
                    reactions=0,
                    thumb_bytes=b"thumb",
                ),
            ],
            music=[
                TelegramMusicItem(
                    file_id=9_007_199_254_740_993,
                    access_hash=-9_007_199_254_740_993,
                    file_reference=b"ref",
                ),
            ],
            music_supported=False,
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", snapshot)

    view = await account_profile_view("acc-view", force_refresh=True)

    assert calls == [("acc-view", True)]
    assert (view.first_name, view.last_name, view.username, view.bio) == ("", None, "u", "")
    assert (view.photos[0].photo_id, view.photos[0].access_hash, view.photos[0].is_main) == (
        "2",
        "-9",
        True,
    )
    assert (view.stories[0].story_id, view.stories[0].views, view.stories[0].reactions) == (
        0,
        0,
        0,
    )
    assert (view.music[0].file_id, view.music[0].access_hash) == (
        "9007199254740993",
        "-9007199254740993",
    )
    assert view.music_supported is False
