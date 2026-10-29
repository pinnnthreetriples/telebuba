"""A sign-in or a read that meets a frozen app, a prompt, or a screen nobody knows.

The scripted container (``_mobile_support``) stands in for the phone. A frozen app (no
dump at all) is restarted (twice at most) and signed in again from its entry screen; one still
frozen, or a screen still unknown after a few BACKs, is a refusal with its own code,
never a silent "signed out". Android's permission dialog is denied, Telegram's contacts
prompt put off, and only a sign-in screen reads as signed out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.emulator.redroid import Instance
from services.accounts import _app_screen, _emulator_sign_in, _mobile_start, open_mobile_progress
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import EmulatorRefusedError, open_account_mobile
from tests.services.accounts._mobile_support import _account, _Container

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen

pytestmark = pytest.mark.usefixtures("mobile_enabled")

_UNRESPONSIVE = "emulator_app_unresponsive"
_SCREEN_UNKNOWN = "emulator_screen_unknown"


@pytest.fixture(autouse=True)
def _no_holds() -> None:
    """No pod is the operator's from an earlier test: warming's open would be busy."""
    emulator_module._OPERATOR.clear()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    logged: list[tuple[str, dict[str, object]]] = []

    async def fake_log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        assert account_id == "acc"
        logged.append((event, extra or {}))

    for module in (_mobile_start, emulator_module, _app_screen):
        monkeypatch.setattr(module, "log_event", fake_log)
    return logged


def _only(steps: list[str], *names: str) -> list[str]:
    return [step for step in steps if step in names]


def _stopped(container: _Container) -> bool:
    return any(step.startswith(("stop:", "remove:")) for step in container.steps)


async def _running_pod(container: _Container, screens: list[Screen]) -> None:
    """A pod left running on the current proxy, its app showing ``screens``."""
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.screens = screens


@pytest.mark.asyncio
async def test_an_app_frozen_after_start_is_restarted_and_signed_in_again(
    container: _Container,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _account()
    container.screens = ["intro", "unresponsive", "intro", "phone", "code", "chats"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (True, None)
    assert _only(container.steps, "start", "restart") == ["start", "restart", "start"]
    assert ("account_mobile_app_restarted", {"reason": _UNRESPONSIVE}) in events
    assert open_mobile_progress("acc").stage == "done"


@pytest.mark.asyncio
async def test_an_app_still_frozen_after_its_restart_is_handed_back_with_its_reason(
    container: _Container,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _account()
    container.screens = ["intro", "unresponsive"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, _UNRESPONSIVE)
    assert container.steps.count("restart") == 2, "restarted twice, never in a loop"
    assert ("account_mobile_sign_in_failed", {"reason": _UNRESPONSIVE}) in events
    progress = open_mobile_progress("acc")
    assert (progress.stage, progress.reason) == ("manual", _UNRESPONSIVE)
    assert not _stopped(container), "the operator's pod is left up to finish by hand"


@pytest.mark.asyncio
async def test_an_app_frozen_twice_in_a_row_is_restarted_twice_and_signed_in(
    container: _Container,
) -> None:
    await _account()
    container.screens = [
        "intro",
        "unresponsive",
        "intro",
        "unresponsive",
        "intro",
        "phone",
        "code",
        "chats",
    ]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (True, None)
    assert container.steps.count("restart") == 2


@pytest.mark.asyncio
async def test_a_frozen_app_never_asks_for_a_second_code_inside_its_pacing(
    container: _Container,
) -> None:
    await _account()
    # Frozen after the number went in: the relaunched app is back on its phone screen.
    container.screens = ["phone", "unresponsive", "phone"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, "device_code_cooldown")
    assert len([step for step in container.steps if step.startswith("phone:")]) == 1


@pytest.mark.asyncio
async def test_a_sign_in_that_ends_on_the_permission_dialog_denies_it(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["phone", "code", "permission", "contacts", "chats"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (True, None)
    assert _only(container.steps, "deny", "not_now") == ["deny", "not_now"]


@pytest.mark.asyncio
async def test_a_sign_in_left_on_its_code_screen_is_handed_back_not_read_as_done(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["phone", "code"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, "emulator_field_missing")


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", ["code", "confirm"])
async def test_a_sign_in_left_on_its_code_or_confirm_screen_goes_back_and_completes(
    container: _Container, screen: Screen
) -> None:
    await _account()
    container.screens = [screen, "phone", "code", "chats"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (True, None)
    assert _only(container.steps, "back") == ["back"]
    assert [step for step in container.steps if step.startswith("phone:")] == [
        "phone:+5215550001111"
    ]
    assert container.steps.index("back") < container.steps.index("cutoff")


@pytest.mark.asyncio
async def test_a_sign_in_that_cannot_leave_its_code_screen_stops_after_a_few_backs(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["code"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, "emulator_field_missing")
    assert container.steps.count("back") == _emulator_sign_in._BACKS_TO_PHONE
    assert not any(step.startswith("phone:") for step in container.steps)


@pytest.mark.asyncio
async def test_a_cloud_password_telegram_does_not_take_is_a_refusal_of_its_own(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["phone", "code", "password", "password"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, "emulator_password_rejected")
    assert container.steps.count("password:cloud-pass") == 1, "typed once, never again"
    assert open_mobile_progress("acc").reason == "emulator_password_rejected"


@pytest.mark.asyncio
async def test_a_running_pod_on_the_permission_dialog_is_signed_in_and_left_up(
    container: _Container,
) -> None:
    await _running_pod(container, ["permission", "contacts", "chats"])

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("already_running", True)
    assert _only(container.steps, "deny", "not_now") == ["deny", "not_now"]
    assert not _stopped(container)


@pytest.mark.asyncio
async def test_a_running_pod_left_inside_a_chat_is_backed_out_to_its_chat_list(
    container: _Container,
) -> None:
    await _running_pod(container, ["unknown", "chats"])

    result = await open_account_mobile("acc", background=True)

    assert result.signed_in is True
    assert _only(container.steps, "back") == ["back"]
    assert not any(step.startswith("phone:") for step in container.steps)


@pytest.mark.asyncio
async def test_a_running_pod_that_stays_unknown_is_a_refusal_not_signed_out(
    container: _Container,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _running_pod(container, ["unknown"])

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc", background=True)

    assert refused.value.code == _SCREEN_UNKNOWN
    assert container.steps.count("back") == _app_screen._BACKS, "a few BACKs, then no more"
    assert ("account_mobile_open_failed", {"reason": _SCREEN_UNKNOWN}) in events
    assert not _stopped(container), "a running pod is not stopped by its read"


@pytest.mark.asyncio
async def test_the_operators_running_pod_on_an_unknown_screen_shows_the_reason(
    container: _Container,
) -> None:
    await _running_pod(container, ["unknown"])

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, _SCREEN_UNKNOWN)
    progress = open_mobile_progress("acc")
    assert (progress.stage, progress.reason) == ("manual", _SCREEN_UNKNOWN)


@pytest.mark.asyncio
async def test_a_frozen_running_app_is_restarted_before_it_is_read(
    container: _Container,
) -> None:
    await _running_pod(container, ["unresponsive", "chats"])

    result = await open_account_mobile("acc", background=True)

    assert result.signed_in is True
    assert container.steps.count("restart") == 1
    assert "back" not in container.steps, "no key goes to a frozen app"


@pytest.mark.asyncio
async def test_a_signed_in_start_on_its_contacts_prompt_reads_signed_in(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["contacts", "chats"]

    result = await open_account_mobile("acc", background=True)

    assert (result.status, result.signed_in) == ("started", True)
    assert "not_now" in container.steps
    assert not any(step.startswith("phone:") for step in container.steps)
