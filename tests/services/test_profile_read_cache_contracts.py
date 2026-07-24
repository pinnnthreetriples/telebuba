"""TTL, isolation, and generation contracts for the live-profile cache."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.telegram_profile_snapshot import (
    TelegramActiveStories,
    TelegramPinnedStories,
    TelegramProfileMusic,
    TelegramProfilePhotos,
    TelegramProfileSnapshot,
)
from services.accounts.profile_read import (
    fetch_live_account_profile,
    invalidate_account_profile_cache,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.accounts import AccountProfileSnapshot


def _empty_results(name: str = "Alice") -> list[object]:
    return [
        TelegramProfileSnapshot(first_name=name),
        TelegramPinnedStories(),
        TelegramActiveStories(),
        TelegramProfileMusic(),
        TelegramProfilePhotos(),
    ]


@pytest.fixture(autouse=True)
def _isolate_cache() -> Iterator[None]:
    invalidate_account_profile_cache()
    yield
    invalidate_account_profile_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize(("age", "expected_calls"), [(9.999, 1), (10.0, 2)])
async def test_ttl_boundary_is_strictly_younger_than_limit(
    monkeypatch: pytest.MonkeyPatch,
    age: float,
    expected_calls: int,
) -> None:
    monkeypatch.setattr(settings.profile_media, "read_snapshot_ttl_seconds", 10)
    calls = 0

    async def execute(_account_id: str, _actions: list[object]) -> list[object]:
        nonlocal calls
        calls += 1
        return _empty_results()

    times = iter([100.0, 100.0 + age, 200.0])
    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)
    monkeypatch.setattr("services.accounts.profile_read.time.time", lambda: next(times))

    await fetch_live_account_profile("acc-ttl")
    await fetch_live_account_profile("acc-ttl")

    assert calls == expected_calls


@pytest.mark.asyncio
async def test_account_cache_entries_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute(account_id: str, _actions: list[object]) -> list[object]:
        calls.append(account_id)
        return _empty_results(account_id)

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)

    first_a = await fetch_live_account_profile("a")
    first_b = await fetch_live_account_profile("b")
    invalidate_account_profile_cache("a")
    second_b = await fetch_live_account_profile("b")
    second_a = await fetch_live_account_profile("a")

    assert first_b is second_b
    assert first_a is not second_a
    assert calls == ["a", "b", "a"]


@pytest.mark.asyncio
async def test_force_refresh_joins_current_generation_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def execute(_account_id: str, _actions: list[object]) -> list[object]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _empty_results()

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)
    ordinary = asyncio.create_task(fetch_live_account_profile("acc-flight"))
    forced: asyncio.Task[AccountProfileSnapshot] | None = None
    try:
        await asyncio.wait_for(started.wait(), timeout=0.5)
        forced = asyncio.create_task(
            fetch_live_account_profile("acc-flight", force_refresh=True),
        )
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(ordinary, forced)
    finally:
        release.set()
        pending = [ordinary, *([forced] if forced is not None else [])]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    assert calls == 1
    assert first is second


@pytest.mark.asyncio
async def test_invalidation_starts_new_generation_without_joining_stale_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def execute(_account_id: str, _actions: list[object]) -> list[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            return _empty_results("stale")
        return _empty_results("fresh")

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", execute)
    stale_task = asyncio.create_task(fetch_live_account_profile("acc-generation"))
    try:
        await asyncio.wait_for(first_started.wait(), timeout=0.5)
        invalidate_account_profile_cache("acc-generation")

        fresh = await fetch_live_account_profile("acc-generation")
        release_first.set()
        stale = await stale_task
        cached = await fetch_live_account_profile("acc-generation")
    finally:
        release_first.set()
        if not stale_task.done():
            stale_task.cancel()
        await asyncio.gather(stale_task, return_exceptions=True)

    assert calls == 2
    assert stale.first_name == "stale"
    assert fresh.first_name == "fresh"
    assert cached is fresh
