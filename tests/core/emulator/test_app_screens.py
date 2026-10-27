"""What a signed-in app shows over its chat list, and an app that shows nothing at all.

A phone that gives no dump is told apart from one whose dump is a screen nobody knows:
the first is a frozen app (restarted), the second something to back out of. Android's
permission dialog is recognised by its resource-ids (``fixtures/permission_dialog.xml``
is a real AOSP dump), Telegram's own contacts prompt by its two buttons in every
language; the contacts permission is denied for good before the app first starts.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import adb, telegram_app
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine
from core.emulator.ui_dump import classify, deny_button, labels, parse_dump

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_SERIAL = "127.0.0.1:5600"
_WEB = "org.telegram.messenger.web"
_PERMISSION_DIALOG = (Path(__file__).parent / "fixtures" / "permission_dialog.xml").read_text(
    encoding="utf-8"
)
_DENY = (98, 874, 622, 986)  # the fixture's «DON'T ALLOW» button


@pytest.fixture(autouse=True)
def _fast_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.emulator, "telegram_package", _WEB)  # whatever APK the host has

    async def no_download() -> None:
        pytest.fail("the phone has its Telegram: no APK is fetched")

    monkeypatch.setattr(telegram_app, "ensure_telegram_apk", no_download)
    monkeypatch.setattr(
        "core.emulator.adb._default_input_engine",
        HumanInputEngine(random.Random(0)),  # noqa: S311 - reproducible test
    )


def _dump(*nodes: str) -> str:
    return '<?xml version="1.0"?><hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>"


def _button(text: str, bounds: str) -> str:
    return f'<node text="{text}" class="android.widget.TextView" bounds="{bounds}" />'


def _contacts_prompt(not_now: str, carry_on: str) -> str:
    return _dump(
        _button("Telegram needs access to your contacts", "[40,500][680,700]"),
        _button(carry_on, "[400,760][680,840]"),
        _button(not_now, "[100,760][380,840]"),
    )


def _dumps(console: Console, answer: str) -> None:
    """Every UI dump answers ``answer`` (uiautomator's own output); the rest answers nothing."""
    console.answer = lambda argv: answer if "uiautomator" in argv else ""


def _taps(console: Console) -> list[tuple[int, int]]:
    swipes = [call for call in console.shell_calls() if call[:2] == ["input", "swipe"]]
    return [(int(call[2]), int(call[3])) for call in swipes]


def _inside(point: tuple[int, int], bounds: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = bounds
    return left <= point[0] <= right and top <= point[1] <= bottom


@pytest.mark.asyncio
async def test_a_phone_that_gives_no_dump_is_unresponsive(console: Console) -> None:
    _dumps(console, "ERROR: null root node returned by UiTestAutomationBridge.")

    assert await adb.dump_ui(_SERIAL) is None
    assert await telegram_app.read_screen(_SERIAL) == ("unresponsive", [])


@pytest.mark.asyncio
async def test_a_dump_of_a_screen_nobody_knows_is_unknown_not_unresponsive(
    console: Console,
) -> None:
    _dumps(
        console,
        "UI hierchary dumped to: /sdcard/x.xml\n" + _dump(_button("Loading…", "[0,0][9,9]")),
    )

    screen, nodes = await telegram_app.read_screen(_SERIAL)

    assert screen == "unknown"
    assert [node.text for node in nodes] == ["Loading…"]


def test_a_real_permission_dialog_is_recognised_by_its_resource_ids() -> None:
    nodes = parse_dump(_PERMISSION_DIALOG)

    assert classify(nodes) == "permission"
    deny = deny_button(nodes)
    assert deny is not None
    assert deny.bounds == _DENY
    assert deny.package == "com.android.permissioncontroller"


def test_the_dialogs_dont_ask_again_button_is_the_deny_it_prefers() -> None:
    nodes = parse_dump(
        _dump(
            '<node resource-id="com.android.permissioncontroller:id/permission_deny_button"'
            ' text="Don\'t allow" class="android.widget.Button" bounds="[0,0][10,10]" />',
            '<node resource-id="com.android.permissioncontroller:id/'
            'permission_deny_and_dont_ask_again_button" text="Don\'t allow" '
            'class="android.widget.Button" bounds="[0,20][10,30]" />',
        )
    )

    deny = deny_button(nodes)

    assert deny is not None
    assert deny.bounds == (0, 20, 10, 30)


@pytest.mark.asyncio
async def test_a_permission_dialog_is_denied_by_its_deny_button(console: Console) -> None:
    await telegram_app.deny_permission(_SERIAL, parse_dump(_PERMISSION_DIALOG))

    taps = _taps(console)
    assert len(taps) == 1
    assert _inside(taps[0], _DENY)


@pytest.mark.parametrize(
    ("not_now", "carry_on"),
    [("Not now", "Continue"), ("Не сейчас", "Продолжить"), ("Jetzt nicht", "Weiter")],
)
@pytest.mark.asyncio
async def test_telegrams_contacts_prompt_is_put_off_in_any_language(
    console: Console, not_now: str, carry_on: str
) -> None:
    nodes = parse_dump(_contacts_prompt(not_now, carry_on))
    assert classify(nodes) == "contacts"

    await telegram_app.put_off_contacts(_SERIAL, nodes)

    taps = _taps(console)
    assert len(taps) == 1
    assert _inside(taps[0], (100, 760, 380, 840)), "«not now», never «continue»"


def test_a_lone_not_now_or_continue_is_not_the_contacts_prompt() -> None:
    words = labels()
    assert "not now" in words.contacts_not_now
    assert "continue" in words.contacts_continue

    lone = parse_dump(_dump(_button("Continue", "[0,0][9,9]")))

    assert classify(lone) == "unknown"


@pytest.mark.asyncio
async def test_a_restart_force_stops_the_app_and_launches_it_again(console: Console) -> None:
    console.answer = lambda argv: f"package:{_WEB}\n" if argv[-2:] == ["list", "packages"] else ""

    await telegram_app.restart_app(_SERIAL)

    calls = [call[:3] for call in console.shell_calls() if call[0] in {"am", "monkey"}]
    assert calls == [["am", "force-stop", _WEB], ["monkey", "--pct-syskeys", "0"]]


@pytest.mark.asyncio
async def test_back_presses_the_back_key(console: Console) -> None:
    await telegram_app.go_back(_SERIAL)

    assert console.shell_calls() == [["input", "keyevent", "4"]]


@pytest.mark.asyncio
async def test_the_contacts_permission_is_denied_for_good_before_the_app_starts(
    console: Console,
) -> None:
    console.answer = lambda argv: f"package:{_WEB}\n" if argv[-2:] == ["list", "packages"] else ""

    await telegram_app.ensure_app(_SERIAL)

    shell = console.shell_calls()
    denial = next(i for i, call in enumerate(shell) if "revoke" in call)
    start = next(i for i, call in enumerate(shell) if call[0] == "monkey")
    assert denial < start, "denied before the app can ask"
    script = " ".join(shell[denial])
    for permission in ("READ_CONTACTS", "WRITE_CONTACTS", "GET_ACCOUNTS"):
        assert f"pm revoke {_WEB} android.permission.{permission}" in script
        assert (
            f"pm set-permission-flags {_WEB} android.permission.{permission} user-set user-fixed"
        ) in script


@pytest.mark.asyncio
async def test_a_phone_that_refuses_the_denial_still_starts_its_app(console: Console) -> None:
    def answer(argv: list[str]) -> str:
        if "revoke" in argv:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return f"package:{_WEB}\n" if argv[-2:] == ["list", "packages"] else ""

    console.answer = answer

    await telegram_app.ensure_app(_SERIAL)

    assert any(call[0] == "monkey" for call in console.shell_calls()), (
        "the permission dialog is denied on screen instead"
    )
