"""A lifecycle rule that drops the LAST watched channel must not wedge the sweep task.

The drop calls ``campaigns.deactivate_channel`` -> ``reconcile_if_running`` -> reconcile
finds an empty watch set (or a warming listener) -> ``_runtime._stop_sweep()``. That stop
runs INSIDE the sweep task, so cancelling-and-awaiting it made the task cancel itself and
then suspend on a gather whose only child is that same task: unbounded recursion, a task
stuck "cancelling" forever, and the drop's log line — the only place the reason exists —
never written. Own module because ``test_runtime_sweep`` sits exactly on the 700-line cap.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    set_listener_account_id,
    set_listener_running,
    stamp_join_request,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _rejoin, _runtime
from services.neurocomment import campaigns as campaigns_service
from tests.services.neurocomment.runtime_support import (
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
    _patch_warming_ids,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")

_CHANNEL = "@gated"


async def _expired_campaign(*channels: str) -> str:
    """Active campaign whose ``@gated`` join request has been pending past the budget.

    Extra ``channels`` are linked but never requested, so dropping ``@gated`` leaves the
    watch set non-empty — the one case where reconcile must NOT stop the sweep.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for channel in channels:
        await link_channel_to_campaign(campaign.campaign_id, channel)
    await create_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    # Assigned, so the give-up rule can see who actually serves the channel.
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await upsert_readiness("acc-1", _CHANNEL, joined=False, captcha_passed=False, ready=False)
    await stamp_join_request("acc-1", _CHANNEL)
    # ``stamp_join_request`` always writes wall-clock now and the sweep loop reads its own
    # clock, so the only way to make the request look expired is to move it back.
    stamp = (datetime.now(UTC) - timedelta(hours=49)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET join_requested_at = ? WHERE channel = ?",
            (stamp, _CHANNEL),
        )
    return campaign.campaign_id


def _patch_no_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the drop's reconcile from spawning the REAL onboarding pass.

    ``deactivate_channel`` -> ``reconcile_if_running`` also pokes onboarding, and with an
    active campaign that reaches ``_probe_account_spam`` -> a live Telethon client, whose
    session sqlite handle then leaks into whichever later test GC happens to run in
    (``filterwarnings = error`` turns it into that test's failure). Nothing here is about
    onboarding, so it is replaced outright.
    """
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda _on_progress: None)


@pytest.fixture
def running_listener(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """A live listener, an instant sweep interval, and a recorder for the tick's tail.

    ``review_access_lost`` is the pass right AFTER the join-request review that drops the
    channel, so recording it is how a test proves the self-stop left the rest of the tick
    intact instead of unwinding it.
    """
    reviewed: list[datetime] = []

    async def _record_rejoin(now: datetime) -> None:
        reviewed.append(now)

    monkeypatch.setattr(_rejoin, "review_access_lost", _record_rejoin)
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0.01)
    _patch_no_onboarding(monkeypatch)
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())
    return reviewed


async def _start_sweep() -> asyncio.Task[None]:
    """Mark the listener running (so a drop reconciles) and start the real sweep task."""
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)
    _runtime._ensure_sweep_running()
    task = _runtime._SWEEP_TASK
    assert task is not None
    return task


async def _await_retirement(task: asyncio.Task[None]) -> None:
    """Wait out one tick; a wedged task fails the test instead of hanging the suite.

    A task that cancelled itself is UNCANCELLABLE, so the timeout must neither await nor
    re-cancel it — awaiting hangs the suite and re-cancelling walks the same recursion the
    bug is made of. Shield the wait, skip a task that already carries a cancel request, and
    leave the wreck for the loop to collect rather than blocking every later test on it.
    """
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except TimeoutError:
        if not task.done() and not task.cancelling():
            task.cancel()  # a merely slow loop is stoppable; a self-cancelled one is not
        pytest.fail("the sweep tick never finished — the task cancelled itself")


async def _gated_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


async def _expiry_rows() -> list[str]:
    return [
        str(entry.extra.get("channel"))
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_join_request_expired"
    ]


@pytest.mark.asyncio
async def test_dropping_the_last_channel_finishes_the_tick_and_retires_the_sweep(
    running_listener: list[datetime],
) -> None:
    """The empty-watch-set stop: the drop is logged, the tick drains, the task ends."""
    campaign_id = await _expired_campaign()

    task = await _start_sweep()
    await _await_retirement(task)

    assert await _gated_is_active(campaign_id) is False
    # The row the operator needs: without it a channel just vanishes with no reason given.
    assert await _expiry_rows() == [_CHANNEL]
    # The passes after the drop still ran — a self-stop retires the loop, it does not
    # unwind the tick that is in flight.
    assert len(running_listener) == 1
    assert not task.cancelled()
    assert _runtime._SWEEP_TASK is None


@pytest.mark.asyncio
async def test_a_warming_listener_found_mid_tick_retires_the_sweep_too(
    monkeypatch: pytest.MonkeyPatch,
    running_listener: list[datetime],
) -> None:
    """The second ``_stop_sweep`` call site: reconcile's warming-listener branch.

    Same self-cancel hazard reached a different way — the drop's reconcile finds the
    listener account warming and tears the subscription down before it ever looks at the
    watch set.
    """
    _patch_warming_ids(monkeypatch, {"listener-1"})
    campaign_id = await _expired_campaign()

    task = await _start_sweep()
    await _await_retirement(task)

    assert await _gated_is_active(campaign_id) is False
    assert await _expiry_rows() == [_CHANNEL]
    assert len(running_listener) == 1
    assert not task.cancelled()
    assert _runtime._SWEEP_TASK is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("running_listener")
async def test_a_drop_that_leaves_another_channel_keeps_the_sweep_running() -> None:
    """Reconcile stops the sweep only when the watch set empties; here it re-subscribes."""
    campaign_id = await _expired_campaign("@other")

    task = await _start_sweep()
    for _ in range(500):  # up to 5s of ticks, same budget as the retirement wait
        await asyncio.sleep(0.01)
        if await _expiry_rows():
            break
    else:
        pytest.fail("the sweep never ran its join-request review")

    assert await _gated_is_active(campaign_id) is False
    assert not task.done()
    assert _runtime._SWEEP_TASK is task
    await _runtime.shutdown_neurocomment_runtime("listener-1")
    assert task.done()


@pytest.mark.asyncio
async def test_a_reconcile_from_outside_the_sweep_still_cancels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator Start / channel edit: the caller is not the sweep, so it is really cancelled.

    Left on the shipped tick interval, so the sweep is parked in its sleep when the cancel
    arrives — exactly the production shape.
    """
    _patch_no_onboarding(monkeypatch)
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    task = _runtime._SWEEP_TASK
    assert task is not None
    assert not task.done()

    # Unlink the only channel from OUTSIDE the sweep, exactly as a channel edit does.
    await campaigns_service.deactivate_channel(campaign.campaign_id, "@a")

    assert _runtime._SWEEP_TASK is None
    assert task.done()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
