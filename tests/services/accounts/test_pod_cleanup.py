"""A pod follows its proxy, and a removal that failed is retried by tombstone only.

``container`` (``_mobile_support``) stands in for the container layer on the service
modules; ``mobile_pods`` imports its own names from ``core.emulator``, replaced here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from core.repositories.pending_cleanups import add_pending_cleanup, fetch_pending_cleanups
from schemas.proxy import ProxySettings
from services.accounts import _pod_cleanup, mobile_pods
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import (
    EmulatorRefusedError,
    open_account_mobile,
    remove_account_container,
    stop_account_pod,
)
from tests.services.accounts._mobile_support import _account, _Container

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.usefixtures("mobile_enabled")

_Events = list[tuple[str, dict[str, object]]]
_PROXY = ProxySettings(proxy_type="socks5", host="10.0.0.5", port=1080)
_AT = "2026-11-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    emulator_module._OPERATOR.clear()
    _pod_cleanup._PROXY_CHANGED.clear()
    monkeypatch.setattr(_pod_cleanup, "_RETRY_PAUSES_SECONDS", (0.0, 0.0, 0.0))


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> _Events:
    logged: _Events = []

    async def fake_log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        del account_id
        logged.append((event, extra or {}))

    for module in (_pod_cleanup, mobile_pods):
        monkeypatch.setattr(module, "log_event", fake_log)
    return logged


async def _settled() -> None:
    await asyncio.gather(*list(_pod_cleanup._STOPS))


def _stops(container: _Container) -> int:
    return container.steps.count("stop:telebuba-acc")


@pytest.mark.asyncio
async def test_a_proxy_change_does_not_wait_long_for_an_open_which_then_aborts_before_signing_in(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_pod_cleanup, "_LOCK_WAIT_SECONDS", 0.05)
    await _account()
    container.launch_gate = asyncio.Event()
    container.screens = ["intro", "phone", "confirm", "code", "password", "chats"]
    start = asyncio.create_task(open_account_mobile("acc"))
    await container.launched.wait()

    await asyncio.wait_for(stop_account_pod("acc"), timeout=2)

    assert "acc" in _pod_cleanup._PROXY_CHANGED, "the open in flight is marked"
    assert _stops(container) == 0, "the stop waits for the open's lock"
    container.launch_gate.set()
    with pytest.raises(EmulatorRefusedError) as refused:
        await start
    await _settled()

    assert refused.value.code == "emulator_not_running"
    assert "cutoff" not in container.steps, "no login code was asked for"
    assert container.steps[-1] == "stop:telebuba-acc"
    assert not _pod_cleanup._PROXY_CHANGED, "the mark goes with the stop"


@pytest.mark.parametrize("failures", [2, 99])
@pytest.mark.asyncio
async def test_a_failed_stop_is_retried_and_logged_once(
    container: _Container,
    events: _Events,
    monkeypatch: pytest.MonkeyPatch,
    failures: int,
) -> None:
    await _account()
    attempts: list[str] = []

    async def flaky(name: str) -> None:
        attempts.append(name)
        if len(attempts) <= failures:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        container.step(f"stop:{name}")

    monkeypatch.setattr(emulator_module, "stop_pod", flaky)

    await stop_account_pod("acc")
    await _settled()

    assert len(attempts) == min(failures + 1, 4), "one try, then at most three more"
    failed = [extra for name, extra in events if name == "account_mobile_pod_stop_failed"]
    assert failed == [{"reason": "emulator_command_failed"}], "once per failure streak"
    assert _stops(container) == (1 if failures == 2 else 0)


@pytest.mark.asyncio
async def test_shutdown_cancels_the_background_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pod_cleanup, "_LOCK_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(_pod_cleanup, "_RETRY_PAUSES_SECONDS", (60.0,))
    await _account()
    emulator_module._LOCKS["acc"] = asyncio.Lock()
    await emulator_module._LOCKS["acc"].acquire()

    await stop_account_pod("acc")
    assert _pod_cleanup._STOPS
    await mobile_pods.cancel_pending_stops()

    assert not _pod_cleanup._STOPS
    assert not _pod_cleanup._PROXY_CHANGED


def _fake_pods(
    monkeypatch: pytest.MonkeyPatch,
    instances: list[Instance],
    on_proxy: Callable[[str], bool],
    proxy_of: Callable[[str], ProxySettings | None],
) -> list[str]:
    stopped: list[str] = []

    async def list_instances() -> list[Instance]:
        return instances

    async def pod_runs_proxy(name: str, _proxy: ProxySettings) -> bool:
        return on_proxy(name)

    async def fetch_proxy(account_id: str) -> ProxySettings | None:
        return proxy_of(account_id)

    async def stop_pod(name: str) -> None:
        stopped.append(name)

    monkeypatch.setattr(mobile_pods, "list_instances", list_instances)
    monkeypatch.setattr(mobile_pods, "pod_runs_proxy", pod_runs_proxy)
    monkeypatch.setattr(mobile_pods, "fetch_account_proxy_settings", fetch_proxy)
    monkeypatch.setattr(mobile_pods, "stop_pod", stop_pod)
    return stopped


@pytest.mark.asyncio
async def test_pods_off_their_proxy_are_stopped_and_only_those(
    events: _Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    instances = [
        Instance(1, "telebuba-fine", running=True),
        Instance(2, "telebuba-moved", running=True),
        Instance(3, "telebuba-noproxy", running=True),
        Instance(4, "telebuba-down", running=False),
        Instance(5, "telebuba_sa-standalone", running=True),
        Instance(6, "telebuba-busy", running=True),
        Instance(7, "telebuba-parked", running=True, paused=True),
    ]
    stopped = _fake_pods(
        monkeypatch,
        instances,
        on_proxy=lambda name: name not in {"telebuba-moved", "telebuba-parked"},
        proxy_of=lambda account_id: None if account_id == "noproxy" else _PROXY,
    )
    emulator_module._LOCKS["busy"] = asyncio.Lock()
    await emulator_module._LOCKS["busy"].acquire()

    count = await mobile_pods.stop_pods_off_proxy()

    assert sorted(stopped) == ["telebuba-moved", "telebuba-noproxy", "telebuba-parked"]
    assert count == 3
    assert ("account_mobile_pod_off_proxy_stopped", {"count": 3}) in events


@pytest.mark.asyncio
async def test_the_off_proxy_sweep_never_raises_and_counts_what_it_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = [Instance(1, "telebuba-a", running=True), Instance(2, "telebuba-b", running=True)]
    stopped = _fake_pods(
        monkeypatch, instances, on_proxy=lambda _n: False, proxy_of=lambda _a: _PROXY
    )

    async def refuse(name: str) -> None:
        if name == "telebuba-a":
            code = "emulator_command_failed"
            raise EmulatorError(code)
        stopped.append(name)

    monkeypatch.setattr(mobile_pods, "stop_pod", refuse)

    assert await mobile_pods.stop_pods_off_proxy() == 1
    assert stopped == ["telebuba-b"]

    async def broken() -> list[Instance]:
        code = "emulator_docker_unavailable"
        raise EmulatorError(code)

    monkeypatch.setattr(mobile_pods, "list_instances", broken)
    assert await mobile_pods.stop_pods_off_proxy() == 0


@pytest.mark.asyncio
async def test_the_off_proxy_sweep_does_nothing_with_the_feature_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = _fake_pods(
        monkeypatch,
        [Instance(1, "telebuba-a", running=True)],
        on_proxy=lambda _n: False,
        proxy_of=lambda _a: _PROXY,
    )
    monkeypatch.setattr(settings.emulator, "enabled", False)

    assert await mobile_pods.stop_pods_off_proxy() == 0
    assert stopped == []


@pytest.mark.asyncio
async def test_the_tombstone_is_written_before_the_removal_and_cleared_after_it(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    async def remove(name: str) -> bool:
        seen.append(await fetch_pending_cleanups("pod"))
        container.step(f"remove:{name}")
        return True

    monkeypatch.setattr(emulator_module, "remove_pod", remove)

    await remove_account_container("acc")

    assert seen == [["telebuba-acc"]]
    assert await fetch_pending_cleanups("pod") == []


@pytest.mark.asyncio
async def test_a_removal_that_found_no_pod_clears_its_tombstone_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nothing(_name: str) -> bool:
        return False

    monkeypatch.setattr(emulator_module, "remove_pod", nothing)

    await remove_account_container("acc")

    assert await fetch_pending_cleanups("pod") == []


@pytest.mark.asyncio
async def test_a_failed_removal_keeps_its_tombstone(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(_name: str) -> bool:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    monkeypatch.setattr(emulator_module, "remove_pod", refuse)

    await remove_account_container("acc")

    assert await fetch_pending_cleanups("pod") == ["telebuba-acc"]
    assert container.steps == []


@pytest.mark.asyncio
async def test_with_the_feature_off_no_tombstone_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_name: str) -> bool:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    monkeypatch.setattr(emulator_module, "remove_pod", refuse)
    monkeypatch.setattr(settings.emulator, "enabled", False)

    await remove_account_container("acc")

    assert await fetch_pending_cleanups("pod") == []


@pytest.mark.asyncio
async def test_startup_retries_what_a_tombstone_names_and_keeps_what_still_fails(
    events: _Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("telebuba-gone", "telebuba-stuck", "telebuba-removed"):
        await add_pending_cleanup("pod", key, requested_at=_AT)
    removed: list[str] = []

    async def remove(name: str) -> bool:
        if name == "telebuba-stuck":
            code = "emulator_command_failed"
            raise EmulatorError(code)
        removed.append(name)
        return name != "telebuba-gone"

    monkeypatch.setattr(mobile_pods, "remove_pod", remove)

    assert await mobile_pods.retry_pending_pod_removals() == 2

    assert sorted(removed) == ["telebuba-gone", "telebuba-removed"]
    assert await fetch_pending_cleanups("pod") == ["telebuba-stuck"]
    assert ("account_mobile_pending_removals_settled", {"count": 2}) in events


@pytest.mark.asyncio
async def test_a_pod_without_a_tombstone_is_never_removed_even_if_its_account_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []

    async def remove(name: str) -> bool:
        removed.append(name)
        return True

    async def listed() -> list[Instance]:
        return [Instance(1, "telebuba-orphan", running=False)]

    monkeypatch.setattr(mobile_pods, "remove_pod", remove)
    monkeypatch.setattr(mobile_pods, "list_instances", listed)

    assert await mobile_pods.retry_pending_pod_removals() == 0
    assert removed == []


@pytest.mark.asyncio
async def test_a_tombstone_of_an_account_that_exists_again_is_dropped_not_acted_on(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    await add_pending_cleanup("pod", "telebuba-acc", requested_at=_AT)
    removed: list[str] = []

    async def remove(name: str) -> bool:
        removed.append(name)
        return True

    monkeypatch.setattr(mobile_pods, "remove_pod", remove)

    assert await mobile_pods.retry_pending_pod_removals() == 1

    assert removed == []
    assert await fetch_pending_cleanups("pod") == []
    assert container.steps == []


@pytest.mark.asyncio
async def test_the_retry_does_nothing_with_the_feature_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_pending_cleanup("pod", "telebuba-a", requested_at=_AT)
    monkeypatch.setattr(settings.emulator, "enabled", False)

    assert await mobile_pods.retry_pending_pod_removals() == 0
    assert await fetch_pending_cleanups("pod") == ["telebuba-a"]
