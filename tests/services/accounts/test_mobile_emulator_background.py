"""Warming's background open, the operator's hold on a pod, and pods left running.

Reuses the scripted container of ``test_mobile_emulator``: a background open shows no
window and signs nothing in, a pod the operator holds is theirs, a window that cannot
open never fails the open, and the pods a crash or a shutdown leaves up are stopped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from services.accounts import mobile_emulator as emulator_module
from services.accounts import mobile_pods
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    open_account_mobile,
    stop_account_pod,
)
from services.accounts.mobile_pods import shutdown_mobile_pods, stop_orphaned_pods
from tests.services.accounts.test_mobile_emulator import (  # noqa: F401 - fixtures
    _account,
    _Container,
    _enabled,
    container,
)

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

_SIGN_IN_STEPS = {"start", "cutoff", "confirm"}


@pytest.fixture
def windows(container: _Container, monkeypatch: pytest.MonkeyPatch) -> set[str]:  # noqa: F811
    """The containers whose window is open; the background path's screen wait faked too."""
    open_windows: set[str] = set()
    monkeypatch.setattr(emulator_module, "viewer_open", lambda name: name in open_windows)
    monkeypatch.setattr(emulator_module, "wait_screen", container.wait_screen)
    emulator_module._OPERATOR.clear()
    emulator_module._ACTIVE.clear()
    return open_windows


@pytest.mark.asyncio
async def test_a_background_start_shows_no_window_and_signs_nothing_in(
    container: _Container,  # noqa: F811
    windows: set[str],  # noqa: ARG001 - fakes the screen wait
) -> None:
    await _account()
    container.screens = ["intro"]

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("started", False)
    assert not [s for s in container.steps if s.startswith("show:") or s in _SIGN_IN_STEPS]
    assert container.steps[-2:] == ["app", "wait"], "the screen is read as the app shows it"
    assert "acc" not in emulator_module._OPERATOR, "warming's open is not the operator's"


@pytest.mark.asyncio
async def test_a_background_open_of_a_running_pod_reads_the_screen_as_it_is(
    container: _Container,  # noqa: F811
    windows: set[str],  # noqa: ARG001 - fakes the screen wait
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.screens = ["phone"]

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("already_running", False)
    assert container.steps == ["find:telebuba-acc", "proxy?:10.0.0.5", "connect", "front", "read"]


@pytest.mark.asyncio
async def test_a_pod_the_operator_holds_is_busy_for_warming_until_its_window_closes(
    container: _Container,  # noqa: F811
    windows: set[str],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    await open_account_mobile("acc")
    windows.add("telebuba-acc")
    assert "acc" in emulator_module._OPERATOR

    with pytest.raises(EmulatorBusyError):
        await open_account_mobile("acc", background=True)

    windows.clear()  # the operator closed the window
    result = await open_account_mobile("acc", background=True)

    assert result.status == "already_running"
    assert "acc" not in emulator_module._OPERATOR


@pytest.mark.asyncio
async def test_the_operators_open_is_busy_while_a_session_runs(
    container: _Container,  # noqa: F811
    windows: set[str],  # noqa: ARG001 - clears the module state
) -> None:
    await _account()
    emulator_module._ACTIVE.add("acc")

    with pytest.raises(EmulatorBusyError):
        await open_account_mobile("acc")

    assert container.steps == []


@pytest.mark.asyncio
async def test_a_proxy_change_ends_the_operators_hold(
    windows: set[str],  # noqa: ARG001 - clears the module state
) -> None:
    await _account()
    await open_account_mobile("acc")

    await stop_account_pod("acc")

    assert "acc" not in emulator_module._OPERATOR


@pytest.mark.asyncio
async def test_a_window_that_cannot_open_does_not_fail_the_open(
    container: _Container,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    container.screens = ["chats"]

    async def no_scrcpy(_name: str, _index: int) -> None:
        code = "emulator_not_found"
        raise EmulatorError(code)

    monkeypatch.setattr(emulator_module, "show_instance", no_scrcpy)

    result = await open_account_mobile("acc")

    assert (result.status, result.signed_in) == ("started", True)
    assert not [s for s in container.steps if s.startswith(("stop:", "remove:"))]


class _Pods:
    """Stands in for ``stop_running_pods``: counts its calls, or raises ``code``."""

    def __init__(self, code: EmulatorRefusalCode | None = None) -> None:
        self.calls = 0
        self.code = code

    async def __call__(self) -> int:
        self.calls += 1
        if self.code is not None:
            raise EmulatorError(self.code)
        return 2


@pytest.mark.parametrize("code", [None, "emulator_not_found", "emulator_command_failed"])
@pytest.mark.asyncio
async def test_pods_left_running_are_stopped_at_startup_and_never_fail_it(
    monkeypatch: pytest.MonkeyPatch,
    code: EmulatorRefusalCode | None,
) -> None:
    pods = _Pods(code)
    monkeypatch.setattr(mobile_pods, "stop_running_pods", pods)

    await stop_orphaned_pods()

    assert pods.calls == 1


@pytest.mark.asyncio
async def test_with_the_feature_off_no_pod_is_touched_at_startup_or_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pods = _Pods()
    monkeypatch.setattr(mobile_pods, "stop_running_pods", pods)
    monkeypatch.setattr(settings.emulator, "enabled", False)

    await stop_orphaned_pods()
    await shutdown_mobile_pods()

    assert pods.calls == 0


@pytest.mark.asyncio
async def test_the_shutdown_stops_every_pod_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pods = _Pods()
    monkeypatch.setattr(mobile_pods, "stop_running_pods", pods)

    await shutdown_mobile_pods()

    assert pods.calls == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("windows")
async def test_a_parked_pod_is_thawed_and_used_as_it_stands(
    container: _Container,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unpause(name: str) -> None:
        container.step(f"unpause:{name}")

    monkeypatch.setattr(emulator_module, "unpause_pod", unpause)
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False, paused=True)
    container.screens = ["chats"]

    result = await open_account_mobile("acc", background=True)

    assert container.steps[:2] == ["find:telebuba-acc", "unpause:telebuba-acc"]
    assert result.status == "already_running", "no boot: the visit starts at once"
    assert not any(step.startswith(("pod:", "boot")) for step in container.steps)


@pytest.mark.asyncio
@pytest.mark.parametrize(("paused", "parked"), [(True, True), (False, False)])
async def test_parking_pauses_the_pod_under_its_lock(
    monkeypatch: pytest.MonkeyPatch, *, paused: bool, parked: bool
) -> None:
    names: list[str] = []

    async def pause(name: str) -> bool:
        names.append(name)
        return paused

    monkeypatch.setattr(mobile_pods, "pause_pod", pause)

    assert await mobile_pods.park_account_pod("acc") is parked
    assert names == ["telebuba-acc"]


@pytest.mark.asyncio
async def test_a_pause_that_fails_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_name: str) -> bool:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    monkeypatch.setattr(mobile_pods, "pause_pod", broken)
    monkeypatch.setattr(mobile_pods, "stop_paused_pod", broken)

    assert await mobile_pods.park_account_pod("acc") is False
    await mobile_pods.stop_parked_pod("acc")  # logged, never raised
