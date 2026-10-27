"""The device farm's start: the phone booted for the operator, its Telegram untouched.

The container layer is the scripted fake on the service modules' own names; these
tests pin that ``boot_account_phone`` boots, seeds and shows the pod on the account's
proxy, and never fetches, installs, starts or signs in the app, so nothing the app or
the account's main session needs can stop the phone from coming up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.emulator.redroid import Instance
from services.accounts import _mobile_start
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import (
    EmulatorRefusedError,
    boot_account_phone,
    open_account_mobile,
    open_mobile_progress,
)
from tests.services.accounts._mobile_support import _account, _Container

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen
    from schemas.emulator import MobileOpenStage

pytestmark = pytest.mark.usefixtures("mobile_enabled")

# Every step the app's install, start, screen reading or sign-in would record.
_APP_STEPS = {"apk", "app", "wait", "read", "front", "start", "cutoff"}


def _app_steps(container: _Container) -> list[str]:
    return [s for s in container.steps if s.split(":")[0] in _APP_STEPS]


@pytest.fixture(autouse=True)
def _no_operator() -> None:
    emulator_module._OPERATOR.clear()


@pytest.mark.asyncio
async def test_a_new_phone_is_booted_on_the_proxy_and_its_app_left_alone(
    container: _Container,
) -> None:
    await _account()
    container.apk_fails = True  # an APK that cannot be had no longer matters

    result = await boot_account_phone("acc")

    assert (result.status, result.signed_in, result.sign_in_refusal) == ("started", False, None)
    started = next(step for step in container.steps if step.startswith("pod:"))
    assert started.endswith(":10.0.0.5")
    for step in ("boot", "show:1", "stealth", "battery", f"seed:{container.specs[0].serial}"):
        assert step in container.steps
    assert _app_steps(container) == []
    assert not any(step.startswith("stop:") for step in container.steps), "the phone stays up"
    assert open_mobile_progress("acc").stage == "done"
    assert emulator_module.operator_holds("acc"), "warming leaves the operator's phone alone"


@pytest.mark.asyncio
async def test_a_running_phone_on_its_proxy_is_shown_as_it_stands(container: _Container) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)

    result = await boot_account_phone("acc")

    assert (result.status, result.signed_in) == ("already_running", False)
    assert [s for s in container.steps if not s.startswith(("find:", "proxy?:"))] == [
        "connect",
        "battery",
        "show:1",
    ]


@pytest.mark.asyncio
async def test_a_phone_on_an_old_proxy_is_restarted_without_its_app(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.on_current_proxy = False

    result = await boot_account_phone("acc")

    assert result.status == "started"
    assert "stop:telebuba-acc" in container.steps
    assert _app_steps(container) == []


@pytest.mark.asyncio
async def test_a_stored_password_input_text_cannot_type_does_not_stop_the_boot(
    container: _Container,
) -> None:
    await _account()
    container.password = "100%"

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")
    booted = await boot_account_phone("acc")

    assert refused.value.code == "emulator_text_unsupported"
    assert booted.status == "started"


@pytest.mark.asyncio
@pytest.mark.parametrize(("data_kept", "undo"), [(False, "remove"), (True, "stop")])
async def test_a_failed_boot_is_refused_and_a_new_pods_fresh_data_removed(
    container: _Container, *, data_kept: bool, undo: str
) -> None:
    await _account()
    container.data_kept = data_kept
    container.fail_at = "boot"

    with pytest.raises(EmulatorRefusedError) as refused:
        await boot_account_phone("acc")

    assert refused.value.code == "emulator_boot_timeout"
    assert container.steps[-1] == f"{undo}:telebuba-acc"
    assert open_mobile_progress("acc").stage == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("background", "screens"),
    [(False, ["phone", "code", "chats"]), (True, ["phone", "phone", "code", "chats"])],
)
async def test_the_accounts_own_open_of_a_booted_phone_installs_and_signs_in_its_app(
    container: _Container,
    *,
    background: bool,
    screens: list[Screen],
) -> None:
    """A running phone with no Telegram is no screen to leave alone: it gets the app."""
    await _account()
    container.app_installed = False
    await boot_account_phone("acc")
    emulator_module._OPERATOR.clear()  # its window closed: warming may take it too
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.steps.clear()
    container.screens = screens

    result = await open_account_mobile("acc", background=background)

    assert (result.status, result.signed_in) == ("already_running", True)
    assert "app" in container.steps
    assert "front" not in container.steps, "no app to bring to the front"
    assert "code:12345" in container.steps
    assert not any(step.startswith(("pod:", "stop:")) for step in container.steps)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "stages"), [(False, ["pod", "boot", "done"]), (True, ["pod", "done"])]
)
async def test_the_dialog_never_shows_an_app_stage_for_a_boot(
    container: _Container,
    monkeypatch: pytest.MonkeyPatch,
    *,
    running: bool,
    stages: list[str],
) -> None:
    await _account()
    if running:
        container.instance = Instance(1, "telebuba-acc", running=True)
    seen: list[str] = []
    real = emulator_module.report_stage

    def spy(account_id: str, stage: MobileOpenStage, reason: str | None = None) -> None:
        seen.append(stage)
        real(account_id, stage, reason)

    for module in (emulator_module, _mobile_start):
        monkeypatch.setattr(module, "report_stage", spy)

    await boot_account_phone("acc")

    assert seen == stages
