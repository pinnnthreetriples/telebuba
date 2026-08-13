"""Tests for the unwatched-channel report: publishing, healing, and shutdown.

Sibling of ``test_runtime_listener.py`` (that file is at the 700-line test cap). Everything
here is about what the runtime *reports* about channels it cannot actually watch — the
report is what the SPA paints its danger strip from, so a torn, stale or falsely-empty
set is a green board over a deaf listener.
"""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import (
    create_campaign,
    link_channel_to_campaign,
    set_listener_account_id,
    set_listener_running,
)
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import ActionResult, JoinChannel
from services.neurocomment import _runtime
from tests.services.neurocomment.runtime_support import (
    _drain_joins,
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
    _patch_warming_ids,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")


async def _seed_channels(*channels: str) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    for channel in channels:
        await link_channel_to_campaign(campaign.campaign_id, channel)


def _gated_join(gate: asyncio.Event) -> object:
    """A ``JoinChannel`` seam that parks until ``gate`` is set.

    Holds the background join task open so a test can change what the listener can
    resolve *before* the task's tail re-subscribes — the real sequence (a channel becomes
    resolvable only once the listener has joined it).
    """

    async def _execute(account_id: str, action: JoinChannel) -> ActionResult:
        await gate.wait()
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    return _execute


@pytest.mark.asyncio
async def test_status_read_mid_reconcile_is_not_torn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A status poll landing inside a reconcile must still see a whole set.

    Reconcile used to clear the set as its first statement and refill it only after
    ``subscribe_posts`` — a window one serial ``get_peer_id`` RPC per uncached channel
    wide, and the channels that fail to resolve are exactly the slow ones. Any poll in
    that window read "nothing unwatched", so the danger strip blinked off on every
    channel link/unlink (each one reconciles) while the SPA polls this endpoint.
    """
    await _seed_channels("@a", "@b")
    _patch_execute(monkeypatch, _ExecuteSpy())
    _patch_listener(monkeypatch, _ListenerSpy(unresolvable={"@b"}))
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)
    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()  # settle the first pass so only our stalled reconcile is in flight

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _stalled_subscribe(
        _account_id: str,
        channels: list[str],
        _on_post: object,
    ) -> list[str]:
        entered.set()
        await release.wait()  # the serial per-channel peer-id resolution
        return [channel for channel in channels if channel != "@b"]

    monkeypatch.setattr(_runtime, "subscribe_posts", _stalled_subscribe)
    reconcile = asyncio.create_task(_runtime.reconcile_neurocomment_runtime("listener-1"))
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    mid = await _runtime.neurocomment_runtime_status()

    # Truly watched: 1. Reporting 2/none-unwatched here re-opens the hole #279 closed.
    assert mid.active_channels == 1
    assert mid.unwatched_channels == ["@b"]

    release.set()
    await reconcile
    assert (await _runtime.neurocomment_runtime_status()).unwatched_channels == ["@b"]
    await _drain_joins()
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_overlapping_reconciles_report_the_last_registered_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With two passes in flight the set must match the last filter registered, not the union.

    ``reconcile_if_running`` is called unlocked from four API-facing sites plus the boot
    hook and nothing single-flights it. Union-updating a set cleared by whichever pass
    started last left a channel the live filter *does* watch flagged as dead to the engine
    — a false red alarm that persisted until the next reconcile.
    """
    await _seed_channels("@a", "@b")
    _patch_execute(monkeypatch, _ExecuteSpy())
    _patch_listener(monkeypatch, _ListenerSpy())
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    # The pass that completes FIRST is the one that failed to resolve @b.
    plans = [({"@b"}, asyncio.Event()), (set[str](), asyncio.Event())]
    entered = [asyncio.Event(), asyncio.Event()]
    calls: list[str] = []

    async def _paired_subscribe(
        account_id: str,
        channels: list[str],
        _on_post: object,
    ) -> list[str]:
        index = min(len(calls), len(plans) - 1)
        calls.append(account_id)
        unresolvable, gate = plans[index]
        entered[index].set()
        await gate.wait()
        return [channel for channel in channels if channel not in unresolvable]

    monkeypatch.setattr(_runtime, "subscribe_posts", _paired_subscribe)
    first = asyncio.create_task(_runtime.reconcile_neurocomment_runtime("listener-1"))
    await asyncio.wait_for(entered[0].wait(), timeout=1.0)
    second = asyncio.create_task(_runtime.reconcile_neurocomment_runtime("listener-1"))
    await asyncio.wait_for(entered[1].wait(), timeout=1.0)

    plans[0][1].set()
    await first
    assert sorted(_runtime._UNWATCHED_CHANNELS) == ["@b"]
    plans[1][1].set()
    await second

    # The healthy pass registered last, so it owns the live filter — and the report.
    assert not _runtime._UNWATCHED_CHANNELS
    await _drain_joins()
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_status_over_a_warming_listener_reports_nothing_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener stopped for warming is DOWN, so no channel may read as watched.

    Reconcile unsubscribes a warming listener and returns without clearing
    ``listener_running`` (nothing paused the runtime — a boot resume or a warming start on
    that account gets here), so status reported ``running=True active_channels=2`` over a
    listener that is not listening: the same bug class #279 closed for unresolvable channels.
    """
    await _seed_channels("@a", "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_warming_ids(monkeypatch, {"listener-1"})
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    status = await _runtime.neurocomment_runtime_status()

    assert status.running is True  # the operator paused nothing, so the flag stays set...
    assert status.active_channels == 0  # ...but nothing is watched, and the SPA must know
    assert status.unwatched_channels == ["@a", "@b"]


@pytest.mark.asyncio
async def test_join_pass_resubscribes_a_channel_that_became_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel that only resolves once joined must get back into the filter by itself.

    ``subscribe_posts`` runs before the paced joins, so a not-yet-joined channel cannot
    resolve to a peer id and is left out of the filter. Nothing re-subscribed afterwards:
    live logs show 16 ``neurocomment_listener_channel_unresolved`` rows for one channel across two
    days and several boots, never healing inside a process — and now permanently red too.
    """
    await _seed_channels("@a", "@b")
    spy = _ListenerSpy(unresolvable={"@b"})
    _patch_listener(monkeypatch, spy)
    gate = asyncio.Event()
    monkeypatch.setattr("services.neurocomment._seams.execute", _gated_join(gate))

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    assert sorted(_runtime._UNWATCHED_CHANNELS) == ["@b"]

    # @b resolves now that the listener is actually in it; let the paced joins drain.
    spy.unresolvable.clear()
    gate.set()
    await _drain_joins()

    assert len(spy.subscribed) == 2
    assert set(spy.subscribed[-1][1]) == {"@a", "@b"}
    assert not _runtime._UNWATCHED_CHANNELS
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_resubscribe_failure_does_not_kill_the_join_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tail heal is best-effort: a raising ``subscribe_posts`` must not kill the pacer.

    The join task also owns the rolling-cap accounting and the coalesced rerun; letting a
    resolve failure escape from its tail would take all of that down with it.
    """
    await _seed_channels("@a", "@b")
    _patch_listener(monkeypatch, _ListenerSpy(unresolvable={"@b"}))
    gate = asyncio.Event()
    monkeypatch.setattr("services.neurocomment._seams.execute", _gated_join(gate))

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    async def _boom(*_args: object) -> list[str]:
        msg = "peer resolution exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(_runtime, "subscribe_posts", _boom)
    gate.set()
    await _drain_joins()  # re-raises whatever the join task raised — must be nothing

    assert sorted(_runtime._UNWATCHED_CHANNELS) == ["@b"]  # report left as it stood
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_shutdown_clears_the_unwatched_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop drops the report with the subscription it described.

    Masked today by the status builder early-returning when not running — but
    ``start_neurocomment`` flips ``listener_running`` BEFORE it reconciles, so a poll
    landing in between is served the *previous* session's channel names.
    """
    await _seed_channels("@a", "@b")
    _patch_listener(monkeypatch, _ListenerSpy(unresolvable={"@b"}))
    _patch_execute(monkeypatch, _ExecuteSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    assert sorted(_runtime._UNWATCHED_CHANNELS) == ["@b"]

    await _runtime.shutdown_neurocomment_runtime("listener-1")

    assert not _runtime._UNWATCHED_CHANNELS


@pytest.mark.asyncio
async def test_reconcile_with_nothing_left_to_watch_reports_the_unsubscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty watch set is a reconcile outcome, so it is logged like every other one.

    That branch stopped the listener, the sweep and the join pass and then returned BEFORE
    either log line: the listener went silent with ``listener_running`` still set and
    nothing said it. Deleting the last campaign is the path where an empty watch set is the
    EXPECTED result — and since that delete now records itself, its consequence being the
    silent half is the worse gap of the two.
    """
    logged: list[tuple[str, str, object]] = []

    async def _fake_log(level: str, event: str, **kwargs: object) -> None:
        logged.append((level, event, kwargs.get("extra")))

    spy = _ListenerSpy()
    _patch_listener(monkeypatch, spy)
    monkeypatch.setattr(_runtime, "log_event", _fake_log)

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    assert spy.stopped == ["listener-1"]  # behaviour unchanged: the listener still goes down
    assert logged == [
        ("INFO", "neurocomment_runtime_reconciled", {"channels": 0, "unwatched": 0}),
    ]


@pytest.mark.asyncio
async def test_cancel_bounded_gives_up_on_a_task_that_ignores_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounded wait must return even when the cancelled task refuses to unwind.

    Nothing covered the timeout branch: dropping either the ``wait_for`` or the
    ``suppress(TimeoutError)`` would make shutdown HANG on one stuck on-post task (or
    crash the shutdown hook) instead of failing a test.
    """
    monkeypatch.setattr(settings.neurocomment, "stop_cancel_timeout_seconds", 0.1)
    cancelled = asyncio.Event()

    async def _swallow_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await asyncio.Event().wait()  # ignores the cancel and keeps holding the loop

    task = asyncio.create_task(_swallow_cancel())
    await asyncio.sleep(0)  # let it reach the await so cancel lands inside the try

    # Bound far above the 0.1s give-up: a regression that waits forever fails here.
    await asyncio.wait_for(_runtime._cancel_bounded(task), timeout=5.0)

    assert cancelled.is_set()  # the cancel WAS delivered; the task just ignored it
    await asyncio.gather(task, return_exceptions=True)  # loop hygiene
