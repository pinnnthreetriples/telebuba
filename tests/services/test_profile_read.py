"""Tests for ``services.accounts.profile_read.fetch_live_account_profile``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountProfileSnapshot
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
from services.accounts.profile_read import (
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


def _stub_execute_read(monkeypatch: pytest.MonkeyPatch, calls: list[object]) -> None:
    async def fake_execute_read(_account_id: str, action: object) -> object:
        calls.append(action)
        if isinstance(action, GetUserProfile):
            return TelegramProfileSnapshot(
                first_name="Alice",
                last_name="Liddell",
                username="alice",
                phone="79991234567",
                bio="Hi there",
                avatar_bytes=b"jpeg",
            )
        if isinstance(action, ListPinnedStories):
            return TelegramPinnedStories(
                items=[TelegramStoryThumb(story_id=101, kind="image", caption="hi")],
            )
        if isinstance(action, ListProfileMusic):
            return TelegramProfileMusic(
                items=[TelegramMusicItem(file_id=555, title="Track", performer="Artist")],
                supported=True,
            )
        msg = f"unexpected action {action}"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read",
        fake_execute_read,
    )


@pytest.mark.asyncio
async def test_fetch_live_profile_returns_combined_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    _stub_execute_read(monkeypatch, calls)

    snapshot = await fetch_live_account_profile("acc-1")

    assert isinstance(snapshot, AccountProfileSnapshot)
    assert snapshot.account_id == "acc-1"
    assert snapshot.first_name == "Alice"
    assert snapshot.bio == "Hi there"
    assert snapshot.avatar_bytes == b"jpeg"
    assert [story.story_id for story in snapshot.stories] == [101]
    assert [track.title for track in snapshot.music] == ["Track"]
    assert snapshot.music_supported is True
    assert snapshot.error is None
    assert snapshot.fetched_at_unix > 0

    action_types = {type(action).__name__ for action in calls}
    assert action_types == {"GetUserProfile", "ListPinnedStories", "ListProfileMusic"}


@pytest.mark.asyncio
async def test_fetch_live_profile_caches_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    _stub_execute_read(monkeypatch, calls)

    snapshot_a = await fetch_live_account_profile("acc-1")
    snapshot_b = await fetch_live_account_profile("acc-1")

    assert snapshot_a is snapshot_b
    assert len(calls) == 3, "Second call inside TTL must hit cache, no extra gateway calls"


@pytest.mark.asyncio
async def test_fetch_live_profile_force_refresh_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    _stub_execute_read(monkeypatch, calls)

    await fetch_live_account_profile("acc-1")
    await fetch_live_account_profile("acc-1", force_refresh=True)

    assert len(calls) == 6, "force_refresh must trigger a fresh gateway round-trip"


@pytest.mark.asyncio
async def test_fetch_live_profile_ttl_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "read_snapshot_ttl_seconds", 1)
    calls: list[object] = []
    _stub_execute_read(monkeypatch, calls)

    # Sequence: stamp call-1 snapshot @1000, _is_fresh check @1500 (past TTL),
    # stamp call-2 snapshot @1500.
    times = iter([1_000.0, 1_500.0, 1_500.0])
    monkeypatch.setattr("services.accounts.profile_read.time.time", lambda: next(times))

    await fetch_live_account_profile("acc-1")
    await fetch_live_account_profile("acc-1")

    assert len(calls) == 6, "Calls past TTL must re-fetch"


@pytest.mark.asyncio
async def test_fetch_live_profile_flood_wait_returns_error_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_read(_account_id: str, _action: object) -> object:
        reason = "FloodWait(42s)"
        raise TelegramReadError(reason)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read",
        fake_execute_read,
    )

    snapshot = await fetch_live_account_profile("acc-flood")

    assert isinstance(snapshot, AccountProfileSnapshot)
    assert snapshot.account_id == "acc-flood"
    assert snapshot.error is not None
    assert "FloodWait" in snapshot.error
    assert snapshot.first_name is None


@pytest.mark.asyncio
async def test_fetch_live_profile_unexpected_error_returns_error_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_read(_account_id: str, _action: object) -> object:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "services.accounts.profile_read.execute_read",
        fake_execute_read,
    )

    snapshot = await fetch_live_account_profile("acc-broken")

    assert snapshot.error is not None
    assert "RuntimeError" in snapshot.error
    assert snapshot.first_name is None


@pytest.mark.asyncio
async def test_invalidate_cache_drops_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    _stub_execute_read(monkeypatch, calls)

    await fetch_live_account_profile("acc-1")
    invalidate_account_profile_cache("acc-1")
    await fetch_live_account_profile("acc-1")

    assert len(calls) == 6, "Invalidated cache must trigger a fresh fetch"
