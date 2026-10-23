"""Warming's background open, the operator's hold on a pod, and pods left running.

Reuses the scripted container of ``test_mobile_emulator``: a background open shows no
window and signs in only an app on an entry screen, a pod the operator holds is
theirs, a window that cannot open never fails the open, and the pods a crash or a
shutdown leaves up are stopped.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from services.accounts import _mobile_start, mobile_pods
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    open_account_mobile,
    stop_account_pod,
)
from services.accounts.mobile_pods import shutdown_mobile_pods, stop_orphaned_pods
from tests.services.accounts._mobile_support import _account, _Container

pytestmark = pytest.mark.usefixtures("mobile_enabled")

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen
    from schemas.emulator import EmulatorRefusalCode

_SIGN_IN_STEPS = {"start", "cutoff", "confirm"}
# Each read takes the next screen: the entry screen is read once by the open, then
# again by the sign-in, as the real app keeps showing it.
_FULL_SIGN_IN: list[Screen] = ["intro", "intro", "phone", "confirm", "code", "password", "chats"]


@pytest.fixture
def windows(container: _Container, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """The containers whose window is open; the background path's screen wait faked too."""
    open_windows: set[str] = set()
    monkeypatch.setattr(emulator_module, "viewer_open", lambda name: name in open_windows)
    monkeypatch.setattr(_mobile_start, "wait_screen", container.wait_screen)
    emulator_module._OPERATOR.clear()
    emulator_module._ACTIVE.clear()
    return open_windows


@pytest.mark.asyncio
async def test_a_background_start_shows_no_window_and_signs_a_fresh_app_in(
    container: _Container,
    windows: set[str],  # noqa: ARG001 - fakes the screen wait
) -> None:
    await _account()
    container.screens = list(_FULL_SIGN_IN)

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("started", True)
    assert not [s for s in container.steps if s.startswith("show:")]
    assert set(container.steps) >= _SIGN_IN_STEPS, "the visit signed the new app in"
    assert "acc" not in emulator_module._OPERATOR, "warming's open is not the operator's"


@pytest.mark.asyncio
async def test_a_background_open_heals_a_running_app_that_fell_signed_out(
    container: _Container,
    windows: set[str],  # noqa: ARG001 - fakes the screen wait
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.screens = ["phone", "phone", "confirm", "code", "password", "chats"]

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("already_running", True)
    assert container.steps[:5] == [
        "find:telebuba-acc",
        "proxy?:10.0.0.5",
        "connect",
        "battery",
        "front",
    ]
    assert "cutoff" in container.steps
    assert not [s for s in container.steps if s.startswith("show:")]


@pytest.mark.parametrize("screen", ["chats", "code"])
@pytest.mark.asyncio
async def test_a_background_open_leaves_any_other_screen_as_it_is(
    container: _Container,
    windows: set[str],  # noqa: ARG001 - fakes the screen wait
    screen: Screen,
) -> None:
    await _account()
    container.screens = [screen]

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("started", screen == "chats")
    assert not [s for s in container.steps if s in _SIGN_IN_STEPS]
    phone = [s for s in container.steps if not s.startswith("seed:")]  # runs alongside
    assert phone[-2:] == ["app", "wait"], "the screen is read as the app shows it"


@pytest.mark.asyncio
async def test_a_pod_the_operator_holds_is_busy_for_warming_until_its_window_closes(
    container: _Container,
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
    container: _Container,
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
    container: _Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    container.screens = ["chats"]

    async def no_scrcpy(_name: str, _index: int) -> None:
        code = "emulator_not_found"
        raise EmulatorError(code)

    monkeypatch.setattr(_mobile_start, "show_instance", no_scrcpy)

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
    container: _Container,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_the_phone_files_are_fetched_at_startup_only_with_the_emulator_on(
    monkeypatch: pytest.MonkeyPatch, *, enabled: bool
) -> None:
    fetched: list[str] = []

    async def apk() -> None:
        fetched.append("apk")
        code = "emulator_download_failed"
        raise EmulatorError(code)  # logged; the next file is still fetched

    async def scrcpy() -> None:
        fetched.append("scrcpy")

    monkeypatch.setattr(mobile_pods, "ensure_telegram_apk", apk)
    monkeypatch.setattr(mobile_pods, "ensure_scrcpy_server", scrcpy)
    monkeypatch.setattr(settings.emulator, "enabled", enabled)

    mobile_pods.prefetch_phone_files()
    await asyncio.gather(*mobile_pods._FETCHES)

    assert fetched == (["apk", "scrcpy"] if enabled else [])
