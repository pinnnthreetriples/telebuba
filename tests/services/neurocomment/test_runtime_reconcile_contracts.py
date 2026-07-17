"""Runtime reconcile and stop/start partial-effect contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.neurocomment import _runtime

pytestmark = pytest.mark.usefixtures("isolate_runtime")


@pytest.mark.asyncio
async def test_warming_listener_is_stopped_without_channel_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "list_warming_account_ids", AsyncMock(return_value={"listener"}))
    stop = AsyncMock()
    channels = AsyncMock()
    sweep = AsyncMock()
    monkeypatch.setattr(_runtime, "stop_post_listener", stop)
    monkeypatch.setattr(_runtime, "list_active_watch_channels", channels)
    monkeypatch.setattr(_runtime, "_stop_sweep", sweep)
    monkeypatch.setattr(_runtime, "log_event", AsyncMock())

    await _runtime.reconcile_neurocomment_runtime("listener")

    stop.assert_awaited_once_with("listener")
    sweep.assert_awaited_once_with()
    channels.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_channels_stops_runtime_without_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "list_warming_account_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        _runtime,
        "list_active_watch_channels",
        AsyncMock(return_value=SimpleNamespace(channels=[])),
    )
    stop = AsyncMock()
    subscribe = AsyncMock()
    monkeypatch.setattr(_runtime, "stop_post_listener", stop)
    monkeypatch.setattr(_runtime, "subscribe_posts", subscribe)
    monkeypatch.setattr(_runtime, "_stop_sweep", AsyncMock())

    await _runtime.reconcile_neurocomment_runtime("listener")

    stop.assert_awaited_once_with("listener")
    subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_join_failure_still_subscribes_full_watch_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "list_warming_account_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        _runtime,
        "list_active_watch_channels",
        AsyncMock(return_value=SimpleNamespace(channels=["@a", "@b"])),
    )
    execute = AsyncMock(
        side_effect=[
            SimpleNamespace(status="failed"),
            SimpleNamespace(status="ok"),
        ]
    )
    subscribe = AsyncMock()
    monkeypatch.setattr(_runtime._seams, "execute", execute)
    monkeypatch.setattr(_runtime, "subscribe_posts", subscribe)
    monkeypatch.setattr(_runtime, "log_event", AsyncMock())
    monkeypatch.setattr(_runtime, "_ensure_sweep_running", lambda: None)

    await _runtime.reconcile_neurocomment_runtime("listener")

    subscribe.assert_awaited_once_with("listener", ["@a", "@b"], _runtime.on_post)
    assert {("listener", "@b")} == _runtime._JOINED_CHANNELS


@pytest.mark.asyncio
async def test_stop_clears_running_flag_even_if_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "get_listener_account_id", AsyncMock(return_value="listener"))
    monkeypatch.setattr(
        _runtime,
        "shutdown_neurocomment_runtime",
        AsyncMock(side_effect=RuntimeError("shutdown")),
    )
    set_running = AsyncMock()
    monkeypatch.setattr(_runtime, "set_listener_running", set_running)

    with pytest.raises(RuntimeError, match="shutdown"):
        await _runtime.stop_neurocomment()

    set_running.assert_awaited_once_with(running=False)


@pytest.mark.asyncio
async def test_clear_forgets_account_and_flag_even_if_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runtime, "get_listener_account_id", AsyncMock(return_value="listener"))
    monkeypatch.setattr(
        _runtime,
        "shutdown_neurocomment_runtime",
        AsyncMock(side_effect=RuntimeError("shutdown")),
    )
    set_account = AsyncMock()
    set_running = AsyncMock()
    monkeypatch.setattr(_runtime, "set_listener_account_id", set_account)
    monkeypatch.setattr(_runtime, "set_listener_running", set_running)

    with pytest.raises(RuntimeError, match="shutdown"):
        await _runtime.clear_neurocomment_listener()

    set_account.assert_awaited_once_with(None)
    set_running.assert_awaited_once_with(running=False)
