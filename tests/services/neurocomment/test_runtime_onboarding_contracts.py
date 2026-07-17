"""Deterministic background-onboarding lifecycle contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.neurocomment import _runtime

pytestmark = pytest.mark.usefixtures("isolate_runtime")


def _campaign(campaign_id: str, status: str = "active") -> SimpleNamespace:
    return SimpleNamespace(campaign_id=campaign_id, status=status)


@pytest.mark.asyncio
async def test_active_campaigns_only_are_onboarded_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _runtime,
        "list_campaigns",
        AsyncMock(
            return_value=SimpleNamespace(
                campaigns=[_campaign("a"), _campaign("paused", "paused"), _campaign("b")]
            )
        ),
    )
    onboard = AsyncMock()
    monkeypatch.setattr(_runtime, "onboard_campaign", onboard)

    await _runtime._onboard_active_campaigns(None)

    assert [call.args[0] for call in onboard.await_args_list] == ["a", "b"]


@pytest.mark.asyncio
async def test_one_campaign_failure_does_not_abort_following_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _runtime,
        "list_campaigns",
        AsyncMock(return_value=SimpleNamespace(campaigns=[_campaign("bad"), _campaign("good")])),
    )
    onboard = AsyncMock(side_effect=[RuntimeError("boom"), None])
    log = AsyncMock()
    monkeypatch.setattr(_runtime, "onboard_campaign", onboard)
    monkeypatch.setattr(_runtime, "log_event", log)

    await _runtime._onboard_active_campaigns(None)

    assert [call.args[0] for call in onboard.await_args_list] == ["bad", "good"]
    log.assert_awaited_once()
    call = log.await_args
    assert call is not None
    assert call.kwargs["extra"] == {
        "campaign_id": "bad",
        "error_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_queued_trigger_runs_exactly_one_additional_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers the former timeout-prone rerun mutations without polling or sleeps."""
    monkeypatch.setattr(
        _runtime,
        "list_campaigns",
        AsyncMock(return_value=SimpleNamespace(campaigns=[_campaign("a")])),
    )
    calls = 0

    async def onboard(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            _runtime._ONBOARD_RERUN = True

    monkeypatch.setattr(_runtime, "onboard_campaign", onboard)

    await _runtime._onboard_active_campaigns(None)

    assert calls == 2
    assert _runtime._ONBOARD_RERUN is False


@pytest.mark.asyncio
async def test_no_queued_trigger_returns_after_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = AsyncMock(return_value=SimpleNamespace(campaigns=[]))
    monkeypatch.setattr(_runtime, "list_campaigns", listing)

    await _runtime._onboard_active_campaigns(None)

    assert listing.await_count == 1


@pytest.mark.asyncio
async def test_ensure_onboarding_coalesces_trigger_while_task_live() -> None:
    async def pending() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(pending())
    _runtime._ONBOARD_TASK = task
    _runtime._ONBOARD_RERUN = False

    try:
        _runtime._ensure_onboarding_running(None)
        _runtime._ensure_onboarding_running(None)

        assert _runtime._ONBOARD_TASK is task
        assert _runtime._ONBOARD_RERUN is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        _runtime._ONBOARD_TASK = None
