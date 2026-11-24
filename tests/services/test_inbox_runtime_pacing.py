"""Inbox starts behind one proxy are serialized with a random gap; other proxies overlap."""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from services import inbox_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(autouse=True)
async def _reset_runtime(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    await inbox_runtime.shutdown_inbox_runtime()
    inbox_runtime._reset_for_tests()
    monkeypatch.setattr(inbox_runtime, "_rng", random.Random(5))  # noqa: S311 - seeded test draw
    # The gap and the monitor never really sleep in tests.
    monkeypatch.setattr(inbox_runtime, "_pause", _record_pause)
    pauses.clear()
    yield
    await inbox_runtime.shutdown_inbox_runtime()
    inbox_runtime._reset_for_tests()


pauses: list[float] = []


async def _record_pause(seconds: float) -> None:
    pauses.append(seconds)


def _account(account_id: str, proxy: str | None) -> SimpleNamespace:
    return SimpleNamespace(account_id=account_id, status="alive", proxy_id=proxy)


class _Subscriber:
    """A subscribe stand-in that records overlap: how many starts ran at once."""

    def __init__(self) -> None:
        self.running = 0
        self.peak_by_proxy: dict[str, int] = {}
        self.running_by_proxy: dict[str, int] = {}
        self.peak = 0
        self.proxy_of: dict[str, str | None] = {}

    async def __call__(self, account_id: str, _callback: object) -> bool:
        proxy = self.proxy_of[account_id] or ""
        self.running += 1
        self.running_by_proxy[proxy] = self.running_by_proxy.get(proxy, 0) + 1
        self.peak = max(self.peak, self.running)
        self.peak_by_proxy[proxy] = max(
            self.peak_by_proxy.get(proxy, 0), self.running_by_proxy[proxy]
        )
        for _ in range(5):
            await asyncio.sleep(0)
        self.running -= 1
        self.running_by_proxy[proxy] -= 1
        return True


@pytest.mark.asyncio
async def test_starts_behind_one_proxy_are_serialized_with_a_gap_and_proxies_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [_account("a1", "p1"), _account("a2", "p1"), _account("a3", "p1")]
    accounts += [_account("b1", "p2"), _account("c1", "p3")]
    subscriber = _Subscriber()
    subscriber.proxy_of = {item.account_id: item.proxy_id for item in accounts}

    async def _list() -> SimpleNamespace:
        return SimpleNamespace(accounts=accounts)

    async def _stop(_account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(inbox_runtime, "list_accounts", _list)
    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscriber)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", _stop)

    await inbox_runtime.reconcile_inboxes_on_startup()
    assert inbox_runtime._STARTUP_TASK is not None
    await inbox_runtime._STARTUP_TASK

    assert subscriber.peak_by_proxy["p1"] == 1  # one at a time behind a shared proxy
    assert subscriber.peak > 1  # different proxies still connect together
    # The second and third start behind p1 each waited a drawn 1..3 s (the pause is patched).
    assert len(pauses) == 2
    assert all(1.0 - 0.5 <= seconds <= 3.0 for seconds in pauses)


@pytest.mark.asyncio
async def test_a_lone_account_and_a_direct_account_are_never_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [_account("a", "p1"), _account("d1", None), _account("d2", None)]
    subscriber = _Subscriber()
    subscriber.proxy_of = {item.account_id: item.proxy_id for item in accounts}

    async def _stop(_account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscriber)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", _stop)
    monkeypatch.setattr(inbox_runtime.settings.telegram, "allow_direct_connection", True)
    inbox_runtime._RUNNING = True

    async def _fetch(account_id: str) -> SimpleNamespace:
        return next(item for item in accounts if item.account_id == account_id)

    monkeypatch.setattr(inbox_runtime, "fetch_account", _fetch)
    for item in accounts:
        await inbox_runtime.start_account_inbox(item.account_id)

    assert pauses == []


@pytest.mark.asyncio
async def test_start_account_inbox_serializes_behind_the_same_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = {"x": _account("x", "p1"), "y": _account("y", "p1")}
    subscriber = _Subscriber()
    subscriber.proxy_of = dict.fromkeys(accounts, "p1")

    async def _fetch(account_id: str) -> SimpleNamespace:
        return accounts[account_id]

    async def _stop(_account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(inbox_runtime, "fetch_account", _fetch)
    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscriber)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", _stop)
    inbox_runtime._RUNNING = True

    await asyncio.gather(
        inbox_runtime.start_account_inbox("x"), inbox_runtime.start_account_inbox("y")
    )

    assert subscriber.peak_by_proxy["p1"] == 1
    assert len(pauses) == 1


@pytest.mark.asyncio
async def test_the_monitor_sleep_is_jittered_by_twenty_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)
        inbox_runtime._RUNNING = False

    async def _ensure(_account_id: str) -> bool:
        return True

    monkeypatch.setattr(inbox_runtime.asyncio, "sleep", _sleep)
    monkeypatch.setattr(inbox_runtime, "ensure_incoming_message_connection", _ensure)
    inbox_runtime._RUNNING = True
    inbox_runtime._ACTIVE.add("acc")

    await inbox_runtime._monitor_account("acc")

    base = inbox_runtime._HEALTH_INTERVAL_SECONDS
    assert base * 0.8 <= slept[0] <= base * 1.2
    assert slept[0] != base


@pytest.mark.asyncio
async def test_a_crowd_behind_one_proxy_does_not_hold_the_start_slots_of_other_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Five accounts queue behind p1 (more than the three start slots), one sits on p2. While
    # the p1 queue waits out its gap, p2's account must already be connected.
    accounts = [_account(f"a{n}", "p1") for n in range(5)] + [_account("b1", "p2")]
    subscriber = _Subscriber()
    subscriber.proxy_of = {item.account_id: item.proxy_id for item in accounts}
    gap = asyncio.Event()
    started_before_gap_ended: list[str] = []
    real = subscriber.__call__

    async def _subscribe(account_id: str, callback: object) -> bool:
        if not gap.is_set():
            started_before_gap_ended.append(account_id)
        return await real(account_id, callback)

    async def _held_gap(_seconds: float) -> None:
        await gap.wait()

    async def _list() -> SimpleNamespace:
        return SimpleNamespace(accounts=accounts)

    async def _stop(_account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(inbox_runtime, "_pause", _held_gap)
    monkeypatch.setattr(inbox_runtime, "list_accounts", _list)
    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", _subscribe)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", _stop)

    await inbox_runtime.reconcile_inboxes_on_startup()
    for _ in range(50):
        await asyncio.sleep(0)

    assert "b1" in started_before_gap_ended
    gap.set()
    assert inbox_runtime._STARTUP_TASK is not None
    await inbox_runtime._STARTUP_TASK
    assert subscriber.peak_by_proxy["p1"] == 1
