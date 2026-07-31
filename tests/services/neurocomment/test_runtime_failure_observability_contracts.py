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

    relevant = [
        (row.level, row.status, row.account_id, row.event, row.extra)
        for row in await list_recent_logs(limit=20)
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
async def test_overload_persists_one_exact_warning_per_dropped_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_concurrent_post_tasks", 2)
    release = asyncio.Event()

    async def blocking_handle(_event: NewPostEvent) -> None:
        await release.wait()

    monkeypatch.setattr(_runtime, "handle_new_post", blocking_handle)

    for post_id in range(5):
        await _runtime.on_post(NewPostEvent(channel="@a", post_id=post_id, text="hi"))

    dropped = [
        (row.level, row.status, row.account_id, row.extra)
        for row in await list_recent_logs(limit=20)
        if row.event == "neurocomment_post_dropped_overloaded"
    ]
    assert dropped == [
        ("WARNING", "warning", None, {"channel": "@a", "in_flight": 2}),
        ("WARNING", "warning", None, {"channel": "@a", "in_flight": 2}),
        ("WARNING", "warning", None, {"channel": "@a", "in_flight": 2}),
    ]

    release.set()
    await _runtime.shutdown_neurocomment_runtime("listener-1")
