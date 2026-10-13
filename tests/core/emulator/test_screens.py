"""The one UI-dump parser and the Telegram screens it tells apart — and the app driver.

The chat list is recognised only on positive evidence; a password field only by the
dump's own ``password="true"``; and no action types into or taps a node the dump did
not show.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import telegram_app
from core.emulator._errors import EmulatorError
from core.emulator.ui_dump import classify, parse_dump, password_field
from schemas.proxy import ProxySettings

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console


def _node(
    text: str = "",
    cls: str = "android.widget.TextView",
    bounds: str = "[0,0][100,50]",
    **attrs: str,
) -> str:
    extra = " ".join(f'{key.replace("_", "-")}="{value}"' for key, value in attrs.items())
    return f'<node text="{text}" class="{cls}" bounds="{bounds}" {extra} />'


def _dump(*nodes: str) -> str:
    return "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">" + (
        "".join(nodes) + "</hierarchy>"
    )


_EDIT = "android.widget.EditText"
CHATS = _dump(_node(content_desc="Open navigation menu"), _node("Telegram"))
INTRO = _dump(_node("Telegram"), _node("Start Messaging", cls="android.widget.Button"))
PHONE = _dump(
    _node("Your phone number"),
    _node("1", cls=_EDIT, bounds="[0,100][80,150]"),
    _node("", cls=_EDIT, bounds="[100,100][400,150]"),
)
CONFIRM = _dump(_node("Is this the correct number?"), _node("Edit"), _node("Yes"))
CODE = _dump(*(_node("", cls=_EDIT, bounds=f"[{i * 60},300][{i * 60 + 50},360]") for i in range(5)))
PASSWORD = _dump(_node("Your password"), _node("", cls=_EDIT, password="true"))


@pytest.mark.parametrize(
    ("xml", "screen"),
    [
        (CHATS, "chats"),
        (INTRO, "intro"),
        (PHONE, "phone"),
        (CONFIRM, "confirm"),
        (CODE, "code"),
        (PASSWORD, "password"),
        ("not xml at all", "unknown"),
        (_dump(_node("Loading…")), "unknown"),
    ],
)
def test_each_telegram_screen_is_told_apart(xml: str, screen: str) -> None:
    assert classify(parse_dump(xml)) == screen


def test_a_field_labelled_password_is_not_a_password_field() -> None:
    """``password="false"`` is the dump saying so, whatever the hint text reads."""
    nodes = parse_dump(_dump(_node("Password", cls=_EDIT, password="false")))

    assert password_field(nodes) is None
    assert classify(nodes) != "password"


def test_a_search_box_open_on_the_chat_list_is_not_the_chat_list() -> None:
    nodes = parse_dump(_dump(_node(content_desc="Open navigation menu"), _node("", cls=_EDIT)))

    assert classify(nodes) != "chats"


def _serve(console: Console, *screens: str) -> None:
    """Answer ``cat`` of the UI dump with ``screens`` in turn (the last one repeats)."""
    queue = list(screens)

    def answer(argv: list[str]) -> str:
        if argv[-2:-1] == ["cat"]:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        if "uiautomator" in argv:
            return "UI hierchary dumped to: /sdcard/telebuba_ui.xml"
        return ""

    console.answer = answer


@pytest.mark.asyncio
async def test_the_phone_is_typed_into_the_two_boxes_the_dump_showed(console: Console) -> None:
    nodes = parse_dump(PHONE)

    await telegram_app.enter_phone("emulator-5554", nodes, "+52 155 5000 1111")

    typed = [call[2] for call in console.shell_calls() if call[:2] == ["input", "text"]]
    taps = [call[2:] for call in console.shell_calls() if call[:2] == ["input", "tap"]]
    assert typed == ["52", "15550001111"]
    assert taps == [["40", "125"], ["250", "125"]]
    assert console.shell_calls()[-1] == ["input", "keyevent", "66"]


@pytest.mark.asyncio
async def test_no_box_on_screen_means_nothing_is_typed_or_tapped(console: Console) -> None:
    with pytest.raises(EmulatorError) as refused:
        await telegram_app.enter_phone("emulator-5554", parse_dump(INTRO), "+15550001111")

    assert refused.value.code == "emulator_field_missing"
    assert console.calls == []


@pytest.mark.asyncio
async def test_an_untypeable_password_is_refused_before_the_screen_is_touched(
    console: Console,
) -> None:
    with pytest.raises(EmulatorError) as refused:
        await telegram_app.enter_password("emulator-5554", parse_dump(PASSWORD), "100%пароль")

    assert refused.value.code == "emulator_text_unsupported"
    assert console.calls == []


@pytest.mark.asyncio
async def test_the_password_goes_only_into_the_dumps_password_field(console: Console) -> None:
    await telegram_app.enter_password("emulator-5554", parse_dump(PASSWORD), "s3cret!")

    assert console.shell_calls() == [
        ["input", "tap", "50", "25"],
        ["input", "text", "s3cret\\!"],
        ["input", "keyevent", "66"],
    ]


@pytest.mark.asyncio
async def test_the_proxy_is_the_apps_own_socks5_setting(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def proxy(_account_id: str) -> ProxySettings:
        return ProxySettings(
            proxy_type="socks5", host="10.0.0.5", port=1080, username="u", password="p&w"
        )

    monkeypatch.setattr(telegram_app, "fetch_account_proxy_settings", proxy)
    _serve(console, _dump(_node("Connect Proxy", cls="android.widget.Button")))

    await telegram_app.apply_account_proxy("emulator-5554", "acc")

    link = next(call for call in console.shell_calls() if call[:2] == ["am", "start"])
    assert link == [
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "'tg://socks?server=10.0.0.5&port=1080&user=u&pass=p%26w'",
        settings.emulator.telegram_package,
    ]
    assert console.shell_calls()[-1] == ["input", "tap", "50", "25"]


@pytest.mark.asyncio
async def test_an_https_proxy_is_refused_the_app_speaks_socks5_only(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def proxy(_account_id: str) -> ProxySettings:
        return ProxySettings(proxy_type="https", host="10.0.0.5", port=443)

    monkeypatch.setattr(telegram_app, "fetch_account_proxy_settings", proxy)

    with pytest.raises(EmulatorError) as refused:
        await telegram_app.apply_account_proxy("emulator-5554", "acc")

    assert refused.value.code == "emulator_proxy_unsupported"
    assert console.calls == []


@pytest.mark.asyncio
async def test_waiting_for_a_screen_returns_the_last_one_seen(console: Console) -> None:
    _serve(console, INTRO, PHONE)

    screen, _nodes = await telegram_app.wait_screen("emulator-5554", {"phone"})
    assert screen == "phone"

    _serve(console, INTRO)
    screen, _nodes = await telegram_app.wait_screen("emulator-5554", {"chats"})
    assert screen == "intro", "time ran out on the intro screen"
