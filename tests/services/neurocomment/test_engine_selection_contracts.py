"""Selection and quota contracts at exact boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from schemas.accounts import AccountRead
from schemas.neurocomment import NeurocommentSettings
from services.neurocomment import _state, engine

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _limits(*, hourly: int = 5, daily: int = 3) -> NeurocommentSettings:
    return NeurocommentSettings(
        max_comments_per_hour=hourly,
        max_comments_per_channel_per_day=daily,
        reply_delay_min_seconds=0,
        reply_delay_max_seconds=1,
        min_trust_score=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("hourly", "daily", "expected"),
    [
        (4, 2, None),
        (5, 2, "quota_hour"),
        (6, 3, "quota_hour"),
        (4, 3, "quota_day"),
    ],
)
def test_quota_boundaries(hourly: int, daily: int, expected: str | None) -> None:
    assert (
        engine._quota_block_reason("account", _limits(), {"account": hourly}, {"account": daily})
        == expected
    )


def test_zero_daily_cap_is_an_off_switch() -> None:
    assert (
        engine._quota_block_reason("account", _limits(daily=0), {"account": 0}, {"account": 999})
        is None
    )


def _pool() -> engine._SelectionPool:
    account = AccountRead(
        account_id="account",
        status="alive",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    return engine._SelectionPool(
        accounts={"account": account},
        ready_account_ids=frozenset({"account"}),
        states={},
        spam={},
        fingerprints={},
        hourly_counts={},
        daily_counts={},
        limits=_limits(),
    )


def test_block_ladder_stops_at_cooldown_before_other_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pool()._replace(accounts={}, ready_account_ids=frozenset())
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: True)

    assert (
        engine._account_block_reason("account", "@channel", 1, datetime.now(UTC), pool)
        == "cooldown"
    )


def test_missing_account_is_not_ready_even_if_readiness_row_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pool()._replace(accounts={})
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: False)

    assert (
        engine._account_block_reason("account", "@channel", 1, datetime.now(UTC), pool)
        == "not_ready"
    )


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ({"not_ready", "unhealthy"}, "unhealthy"),
        ({"cooldown", "quota_day"}, "quota_day"),
        ({"quota_hour", "quota_day"}, "quota_hour"),
    ],
)
def test_selection_miss_reports_highest_priority_blocker(
    monkeypatch: pytest.MonkeyPatch, reasons: set[str], expected: str
) -> None:
    accounts = sorted(reasons)
    monkeypatch.setattr(
        engine,
        "_account_block_reason",
        lambda account_id, *_args: account_id,
    )

    assert (
        engine._selection_block_reason(accounts, "@channel", 1, datetime.now(UTC), _pool())
        == expected
    )


@pytest.mark.asyncio
async def test_same_account_critical_sections_are_serialized() -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first() -> None:
        async with engine._account_lock("account"):
            order.append("first-enter")
            first_entered.set()
            await release_first.wait()
            order.append("first-exit")

    async def second() -> None:
        async with engine._account_lock("account"):
            order.append("second-enter")

    first_task = asyncio.create_task(first())
    tasks = [first_task]
    blocked_order: list[str] = []
    task_results: list[object] = []
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=0.5)
        second_task = asyncio.create_task(second())
        tasks.append(second_task)
        await asyncio.sleep(0)
        blocked_order = list(order)
    finally:
        release_first.set()
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

    assert blocked_order == ["first-enter"]
    assert order == ["first-enter", "first-exit", "second-enter"]
    assert task_results == [None, None]


@pytest.mark.asyncio
async def test_different_accounts_can_hold_critical_sections_concurrently() -> None:
    both_entered = asyncio.Event()
    active = 0
    peak = 0

    async def worker(account_id: str) -> None:
        nonlocal active, peak
        async with engine._account_lock(account_id):
            active += 1
            peak = max(peak, active)
            if active == 2:
                both_entered.set()
            await both_entered.wait()
            active -= 1

    tasks = [asyncio.create_task(worker("a")), asyncio.create_task(worker("b"))]
    observed_peak = 0
    task_results: list[object] = []
    try:
        await asyncio.wait_for(both_entered.wait(), timeout=0.5)
        observed_peak = peak
    finally:
        both_entered.set()
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

    assert observed_peak == 2
    assert task_results == [None, None]
