"""Failure-state and audit contracts for the public neurocomment runtime."""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import (
    create_campaign,
    get_listener_account_id,
    get_listener_running,
    link_channel_to_campaign,
    list_recent_logs,
    set_listener_account_id,
    set_listener_running,
)
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _runtime
from tests.services.neurocomment.runtime_support import (
    _drain_joins,
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_name", "expected_listener"),
    [
        ("stop_neurocomment", "listener-1"),
        ("clear_neurocomment_listener", None),
    ],
)
async def test_teardown_commits_persisted_state_when_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    expected_listener: str | None,
) -> None:
    """Pause/remove state must be durable even if runtime cleanup raises."""
    await set_listener_account_id("listener-1")
    await set_listener_running(running=True)

    async def fail_shutdown(_account_id: str) -> None:
        message = "shutdown failed"
        raise RuntimeError(message)

    monkeypatch.setattr(_runtime, "shutdown_neurocomment_runtime", fail_shutdown)

    operation = getattr(_runtime, operation_name)
    with pytest.raises(RuntimeError, match="shutdown failed"):
        await operation()

    assert await get_listener_account_id() == expected_listener
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_join_failure_persists_complete_audit_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy(ok=False))

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    # Sorted by event name rather than read in journal order: nothing orders these two
    # rows. ``_reconcile_owned`` hands the paced join pass to a background task via
    # ``_ensure_join_running`` and writes ``neurocomment_runtime_reconciled`` after that,
    # while ``run_join_pass`` writes ``neurocomment_listener_join_failed`` from the task
    # and takes no lifecycle lock; ``log_event`` then hands each insert to a worker
    # thread, which is where SQLite assigns the id — so the call that submits its insert
    # first can still lose the autoincrement. The rest stays exact: the whole list is
    # compared, so a missing row, a duplicate, or one altered
    # level/status/account_id/extra field still fails.
    relevant = [
        (row.level, row.status, row.account_id, row.event, row.extra)
        for row in sorted(await list_recent_logs(limit=20), key=lambda row: row.event)
        if row.event
        in {
            "neurocomment_listener_join_failed",
            "neurocomment_runtime_reconciled",
        }
    ]
    assert relevant == [
        (
            "WARNING",
            "warning",
            "listener-1",
            "neurocomment_listener_join_failed",
            {"channel": "@a", "status": "failed"},
        ),
        (
            "INFO",
            "success",
            "listener-1",
            "neurocomment_runtime_reconciled",
            {"channels": 1, "unwatched": 0},
        ),
    ]

    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_a_full_inbox_persists_one_exact_warning_per_refused_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overload no longer drops posts, but a FULL inbox still refuses them.

    Excess work now waits in SQLite instead of being lost, so the only post that
    never gets processed is one the bounded queue refuses outright. That refusal
    is invisible unless it is reported, and the report has to name the exact
    channel and post so an operator can tell what was lost from what was merely
    delayed.
    """
    monkeypatch.setattr(settings.neurocomment, "post_inbox_max_pending", 2)
    monkeypatch.setattr(settings.neurocomment, "max_concurrent_post_tasks", 1)
    release = asyncio.Event()

    async def blocking_handle(_event: NewPostEvent) -> None:
        await release.wait()

    monkeypatch.setattr(_runtime, "handle_new_post", blocking_handle)

    for post_id in range(5):
        await _runtime.on_post(NewPostEvent(channel="@a", post_id=post_id, text="hi"))
    release.set()

    refused = [
        (row.level, row.extra)
        for row in await list_recent_logs(limit=20)
        if row.event == "neurocomment_inbox_queue_full"
    ]

    assert refused, "a refused post must be reported, not silently discarded"
    assert [level for level, _extra in refused] == ["WARNING"] * len(refused)
    refused_ids = [extra["post_id"] for _level, extra in refused]
    assert [extra for _level, extra in refused] == [
        {"channel": "@a", "post_id": post_id} for post_id in refused_ids
    ]
    assert len(set(refused_ids)) == len(refused_ids), "one warning per refused post, not repeats"
    assert set(refused_ids) <= {0, 1, 2, 3, 4}
