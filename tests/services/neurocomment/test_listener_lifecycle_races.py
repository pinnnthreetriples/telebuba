"""Global lifecycle ownership across start, reconcile, clear and account delete."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.config import settings
from core.db import (
    create_account,
    create_campaign,
    fetch_account,
    get_listener_account_id,
    get_listener_running,
    link_channel_to_campaign,
    set_listener_account_id,
    set_listener_running,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.accounts.lifecycle import remove_account
from services.neurocomment import _runtime
from services.neurocomment._join import run_join_pass

pytestmark = pytest.mark.usefixtures("isolate_runtime")


@pytest.mark.asyncio
async def test_concurrent_listener_switches_publish_only_last_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    reconciled: list[str] = []

    async def _reconcile(account_id: str) -> None:
        reconciled.append(account_id)
        if account_id == "a":
            entered.set()
            await release.wait()

    async def _no_warming() -> set[str]:
        return set()

    monkeypatch.setattr(_runtime, "reconcile_neurocomment_runtime", _reconcile)
    monkeypatch.setattr(_runtime, "list_warming_account_ids", _no_warming)
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda _progress: None)

    first = asyncio.create_task(_runtime.start_neurocomment("a"))
    await entered.wait()
    second = asyncio.create_task(_runtime.start_neurocomment("b"))
    await asyncio.wait_for(second, timeout=0.2)
    assert reconciled == ["a", "b"]
    release.set()
    await first

    assert reconciled == ["a", "b"]
    assert await get_listener_account_id() == "b"
    assert await get_listener_running() is True


@pytest.mark.asyncio
async def test_clear_invalidates_inflight_reconcile_without_waiting_for_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")
    await set_listener_account_id("a")
    await set_listener_running(running=True)
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def _subscribe(_account: str, channels: list[str], _callback: object) -> list[str]:
        calls.append("subscribe")
        entered.set()
        await release.wait()
        return channels

    async def _stop(_account: str) -> None:
        calls.append("stop")

    monkeypatch.setattr(_runtime, "subscribe_posts", _subscribe)
    monkeypatch.setattr(_runtime, "stop_post_listener", _stop)

    async def _no_backfill(*_args: object) -> None:
        return None

    monkeypatch.setattr(_runtime._inbox_runtime, "ensure_backfill", _no_backfill)
    reconcile = asyncio.create_task(_runtime.reconcile_neurocomment_runtime("a"))
    await entered.wait()
    clear = asyncio.create_task(_runtime.clear_neurocomment_listener())
    await asyncio.wait_for(clear, timeout=0.2)
    assert calls == ["subscribe", "stop"]
    release.set()
    await reconcile

    # The first stop is the operator's clear. The second is the stale reconcile's
    # service-boundary cleanup after its alternate gateway completes late.
    assert calls == ["subscribe", "stop", "stop"]
    assert await get_listener_account_id() is None
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_old_subscribe_cannot_stop_new_same_account_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    stopped = 0
    live = {"owner": None}

    async def _subscribe(_account: str, channels: list[str], _callback: object) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            # Model core's subscription-generation fence: the old pass returns last
            # but does not overwrite the handler committed by generation two.
            return []
        live["owner"] = "new"
        return channels

    async def _stop(_account: str) -> None:
        nonlocal stopped
        stopped += 1
        live["owner"] = None

    async def _no_warming() -> set[str]:
        return set()

    async def _nothing(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0)
    monkeypatch.setattr(_runtime, "subscribe_posts", _subscribe)
    monkeypatch.setattr(_runtime, "stop_post_listener", _stop)
    monkeypatch.setattr(_runtime, "list_warming_account_ids", _no_warming)
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda _progress: None)
    monkeypatch.setattr(_runtime, "_ensure_join_running", lambda _account: None)
    monkeypatch.setattr(_runtime._inbox_runtime, "start_inbox", _nothing)
    monkeypatch.setattr(_runtime._inbox_runtime, "stop_inbox", _nothing)
    monkeypatch.setattr(_runtime._inbox_runtime, "ensure_backfill", _nothing)

    old = asyncio.create_task(_runtime.start_neurocomment("a"))
    await entered.wait()
    await _runtime.stop_neurocomment()
    await _runtime.start_neurocomment("a")
    assert live["owner"] == "new"

    release.set()
    await old

    assert stopped == 1
    assert live["owner"] == "new"
    assert await get_listener_account_id() == "a"
    assert await get_listener_running() is True


@pytest.mark.asyncio
async def test_deleting_listener_account_cleans_runtime_before_db_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="listener", label="L", session_name="listener"))
    await set_listener_account_id("listener")
    await set_listener_running(running=True)
    stopped: list[str] = []

    async def _stop(account_id: str) -> None:
        stopped.append(account_id)

    monkeypatch.setattr(_runtime, "stop_post_listener", _stop)
    await remove_account("listener")

    assert stopped == ["listener"]
    assert await get_listener_account_id() is None
    assert await get_listener_running() is False
    assert await fetch_account("listener") is None


@pytest.mark.asyncio
async def test_stale_reconcile_cannot_replace_new_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _subscribe(account_id: str, channels: list[str], _callback: object) -> list[str]:
        calls.append(account_id)
        return channels

    async def _no_warming() -> set[str]:
        return set()

    monkeypatch.setattr(_runtime, "subscribe_posts", _subscribe)
    monkeypatch.setattr(_runtime, "list_warming_account_ids", _no_warming)
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda _progress: None)
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")

    await _runtime.start_neurocomment("a")
    await _runtime.start_neurocomment("b")
    before = list(calls)
    await _runtime.reconcile_neurocomment_runtime("a")

    assert calls == before
    assert await get_listener_account_id() == "b"


@pytest.mark.asyncio
async def test_switch_generation_stops_old_join_before_next_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    entered, release = asyncio.Event(), asyncio.Event()
    joined: list[str] = []

    async def _execute(_account: str, action: object) -> object:
        from schemas.telegram_actions import ActionResult  # noqa: PLC0415

        channel = action.channel  # ty: ignore[unresolved-attribute]
        joined.append(channel)
        entered.set()
        await release.wait()
        return ActionResult(status="failed", action_type="join_channel", account_id="a")

    monkeypatch.setattr("services.neurocomment._seams.execute", _execute)
    generation = _runtime._activate_runtime_owner("a")
    task = asyncio.create_task(run_join_pass("a", generation=generation))
    await entered.wait()
    _runtime._invalidate_runtime_owner("a")
    _runtime._activate_runtime_owner("b")
    release.set()
    await task

    assert joined == ["@a"]


@pytest.mark.asyncio
async def test_cancel_bounded_retains_task_that_suppresses_every_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "stop_cancel_timeout_seconds", 0.01)
    stop = asyncio.Event()
    cancellations = 0

    async def _stubborn() -> None:
        nonlocal cancellations
        while not stop.is_set():
            try:
                await stop.wait()
            except asyncio.CancelledError:
                cancellations += 1

    task = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)
    pending = await _runtime._cancel_bounded(task)

    assert task in pending
    assert task in _runtime._RETIRED_TASKS
    assert cancellations >= 1
    stop.set()
    await task
    await asyncio.sleep(0)
    assert task not in _runtime._RETIRED_TASKS


@pytest.mark.asyncio
async def test_stubborn_stopping_sweep_blocks_replacement_until_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 1)
    monkeypatch.setattr(settings.neurocomment, "stop_cancel_timeout_seconds", 0.01)
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    starts = 0

    async def _stubborn_sweep() -> None:
        nonlocal starts
        starts += 1
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()

    monkeypatch.setattr(_runtime, "_sweep_loop", _stubborn_sweep)
    _runtime._ensure_sweep_running()
    old = _runtime._SWEEP_TASK
    assert old is not None
    await started.wait()
    await _runtime._stop_sweep()
    await cancelled.wait()

    assert _runtime._SWEEP_TASK is None
    assert _runtime._SWEEP_STOPPING_TASK is old
    _runtime._ensure_sweep_running()
    assert _runtime._SWEEP_TASK is None
    assert _runtime._SWEEP_STOPPING_TASK is old
    assert starts == 1

    release.set()
    await old
    await asyncio.sleep(0)
    assert _runtime._SWEEP_TASK is None
    assert _runtime._SWEEP_STOPPING_TASK is None
    _runtime._ensure_sweep_running()
    await asyncio.sleep(0)
    assert starts == 2


@pytest.mark.asyncio
async def test_switch_spawns_new_onboarding_and_stale_owner_stops_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    second = await create_campaign(CampaignCreate(name="C2", prompt="p"))
    entered, release = asyncio.Event(), asyncio.Event()
    stale_task: asyncio.Task[None] | None = None
    stale_calls: list[str] = []
    current_calls: list[str] = []

    async def _onboard(campaign_id: str, *, on_progress: object = None) -> None:
        del on_progress
        task = asyncio.current_task()
        if task is stale_task:
            stale_calls.append(campaign_id)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return
        current_calls.append(campaign_id)

    monkeypatch.setattr(_runtime, "onboard_campaign", _onboard)
    _runtime._activate_runtime_owner("a")
    _runtime._ensure_onboarding_running(None)
    stale_task = _runtime._ONBOARD_TASK
    assert stale_task is not None
    await entered.wait()

    _runtime._invalidate_runtime_owner("a")
    _runtime._activate_runtime_owner("b")
    _runtime._ensure_onboarding_running(None)
    current_task = _runtime._ONBOARD_TASK
    assert current_task is not None
    assert current_task is not stale_task
    await current_task
    release.set()
    await stale_task

    assert stale_calls == [first.campaign_id]
    assert current_calls == [first.campaign_id, second.campaign_id]


@pytest.mark.asyncio
async def test_switch_mid_onboarding_fences_next_telegram_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway suppressing cancellation cannot let retired A issue RPC number two."""
    from services.neurocomment import onboarding  # noqa: PLC0415

    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def _refresh(account_id: str, *, force: bool = False) -> object:
        del force
        calls.append(account_id)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Model the hostile dependency from the review: it consumes cancellation
            # and returns normally. The generation fence after the await must still win.
            await release.wait()
        return SimpleNamespace(checked_at="1970-01-01T00:00:00+00:00")

    monkeypatch.setattr("services.neurocomment._seams.refresh_spam_status", _refresh)
    generation = _runtime._activate_runtime_owner("a")

    async def _owned_pass() -> None:
        from services.neurocomment._onboarding_owner import generation_fence  # noqa: PLC0415

        with generation_fence(lambda: _runtime._runtime_owner_is_current("a", generation)):
            await onboarding._probe_account_spam(["member-1", "member-2"])

    task = asyncio.create_task(_owned_pass())
    await entered.wait()
    _runtime._invalidate_runtime_owner("a")
    _runtime._activate_runtime_owner("b")
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == ["member-1"]
