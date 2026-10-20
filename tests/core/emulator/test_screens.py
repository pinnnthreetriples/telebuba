"""The one UI-dump parser and the Telegram screens it tells apart — and the app driver.

The chat list is recognised only on positive evidence; a password field only by the
dump's own ``password="true"``; and no action types into or taps a node the dump did
not show.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from core.emulator import telegram_app
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine
from core.emulator.ui_dump import classify, labels, parse_dump, password_field

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.emulator.ui_dump import UiNode
    from tests.core.emulator.conftest import Console


@pytest.fixture(autouse=True)
def _fast_input(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_s: float) -> None:
        return

    monkeypatch.setattr("core.emulator.adb.asyncio.sleep", _instant_sleep)
    monkeypatch.setattr(
        "core.emulator.adb._default_input_engine",
        HumanInputEngine(random.Random(0)),  # noqa: S311 - reproducible test
    )


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


@pytest.mark.parametrize(
    ("xml", "screen"),
    [
        (_dump(_node(content_desc="Відкрити меню навігації")), "chats"),
        (_dump(_node(content_desc="Yeni Mesaj")), "chats"),
        (
            _dump(_node("Telegram"), _node("Empezar a chatear", cls="android.widget.Button")),
            "intro",
        ),
        (_dump(_node("Правильно ли указан номер?"), _node("Да")), "confirm"),
        (_dump(_node("Ist das die richtige Nummer?"), _node("Ja")), "confirm"),
        (
            _dump(
                _node("Tu número de teléfono"),
                _node("52", cls=_EDIT, bounds="[0,100][80,150]"),
                _node("", cls=_EDIT, bounds="[100,100][400,150]"),
            ),
            "phone",
        ),
    ],
)
def test_a_container_in_another_language_is_read_alike(xml: str, screen: str) -> None:
    """The container runs in its fingerprint's language: Telegram's own strings for it."""
    assert classify(parse_dump(xml)) == screen


def test_every_label_group_has_english_and_other_languages() -> None:
    words = labels()

    assert "open navigation menu" in words.chats
    assert "правильно ли указан номер?" in words.confirm, "Telegram's Russian wording"
    for group in (words.start, words.yes, words.chats, words.phone, words.confirm):
        assert len(group) > 10
    assert "channel" in words.channel
    assert "канал" in words.channel
    assert all(len(stem) >= 3 for stem in words.story)


def test_a_field_labelled_password_is_not_a_password_field() -> None:
    """``password="false"`` is the dump saying so, whatever the hint text reads."""
    nodes = parse_dump(_dump(_node("Password", cls=_EDIT, password="false")))

    assert password_field(nodes) is None
    assert classify(nodes) != "password"


def test_a_search_box_open_on_the_chat_list_is_not_the_chat_list() -> None:
    nodes = parse_dump(_dump(_node(content_desc="Open navigation menu"), _node("", cls=_EDIT)))

    assert classify(nodes) != "chats"


def _serve(console: Console, *screens: str) -> None:
    """Answer the UI dump script with ``screens`` in turn (the last one repeats)."""
    queue = list(screens)

    def answer(argv: list[str]) -> str:
        if "uiautomator" in argv:
            screen = queue.pop(0) if len(queue) > 1 else queue[0]
            return f"UI hierchary dumped to: /sdcard/telebuba_ui.xml\n{screen}"
        return ""

    console.answer = answer


@pytest.mark.asyncio
async def test_the_phone_is_typed_into_the_two_boxes_the_dump_showed(console: Console) -> None:
    nodes = parse_dump(PHONE)

    await telegram_app.enter_phone("emulator-5554", nodes, "+52 155 5000 1111")

    typed = [call[2] for call in console.shell_calls() if call[:2] == ["input", "text"]]
    # The prefilled country box is tapped to be emptied, then each box to be typed into.
    swipes = [call[2:] for call in console.shell_calls() if call[:2] == ["input", "swipe"]][1:]
    assert "".join(typed) == "5215550001111"
    assert len(swipes) == 2
    # First box bounds: [0, 100][80, 150]
    assert 0 <= int(swipes[0][0]) <= 80
    assert 100 <= int(swipes[0][1]) <= 150
    # Second box bounds: [100, 100][400, 150]
    assert 100 <= int(swipes[1][0]) <= 400
    assert 100 <= int(swipes[1][1]) <= 150
    assert console.shell_calls()[-1] == ["input", "keyevent", "66"]


class _PhoneScreen:
    """Telegram's two phone boxes as the app runs them.

    A tap leaves the caret where it lands (here: at the start, the worst case) and
    MOVE_END takes it to the end; a DEL in an empty number box goes back into the
    country code and deletes there.
    """

    def __init__(self, country: str, number: str) -> None:
        self.boxes = [country, number]
        self.focus = 0
        self.caret = 0

    def _key(self, key: str) -> None:
        if key == "123":
            self.caret = len(self.boxes[self.focus])
        elif key == "67":
            if not self.boxes[self.focus] and self.focus == 1:
                self.focus = 0
                self.caret = len(self.boxes[0])
            box = self.boxes[self.focus]
            if self.caret:
                self.boxes[self.focus] = box[: self.caret - 1] + box[self.caret :]
                self.caret -= 1

    def __call__(self, argv: list[str]) -> str:
        command = argv[argv.index("shell") + 1 :]
        if command[:2] == ["input", "swipe"]:
            self.focus = 0 if int(command[2]) < 90 else 1  # the boxes' edge
            self.caret = 0
        elif command[:2] == ["input", "text"]:
            box = self.boxes[self.focus]
            self.boxes[self.focus] = box[: self.caret] + command[2] + box[self.caret :]
            self.caret += len(command[2])
        elif command[:2] == ["input", "keyevent"]:
            for key in command[2:]:
                self._key(key)
        return ""


@pytest.mark.parametrize(
    ("country", "number"),
    [("", ""), ("1", ""), ("7", "999 123 45 67"), ("", "555")],
)
@pytest.mark.asyncio
async def test_the_boxes_end_up_holding_exactly_the_number(
    console: Console, country: str, number: str
) -> None:
    screen = _PhoneScreen(country, number)
    console.answer = screen
    nodes = parse_dump(
        _dump(
            _node("Your phone number"),
            _node(country, cls=_EDIT, bounds="[0,100][80,150]"),
            _node(number, cls=_EDIT, bounds="[100,100][400,150]"),
        )
    )

    await telegram_app.enter_phone("emulator-5554", nodes, "+49 151 1234 5678")

    assert screen.boxes == ["49", "15112345678"]


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

    swipes = [call for call in console.shell_calls() if call[:2] == ["input", "swipe"]]
    assert len(swipes) == 1
    # Password field bounds [0, 0][100, 50]
    x, y = int(swipes[0][2]), int(swipes[0][3])
    assert 0 <= x <= 100
    assert 0 <= y <= 50

    typed = [call[2] for call in console.shell_calls() if call[:2] == ["input", "text"]]
    assert typed == ["s", "3", "c", "r", "e", "t", "\\!"]
    assert console.shell_calls()[-1] == ["input", "keyevent", "66"]


@pytest.mark.asyncio
async def test_waiting_for_a_screen_returns_the_last_one_seen(console: Console) -> None:
    _serve(console, INTRO, PHONE)

    screen, _nodes = await telegram_app.wait_screen("emulator-5554", {"phone"})
    assert screen == "phone"

    _serve(console, INTRO)
    screen, _nodes = await telegram_app.wait_screen("emulator-5554", {"chats"})
    assert screen == "intro", "time ran out on the intro screen"


@pytest.mark.asyncio
async def test_press_start_calls_human_tap(console: Console) -> None:
    nodes = parse_dump(INTRO)
    await telegram_app.press_start("emulator-5554", nodes)
    swipes = [call for call in console.shell_calls() if call[:2] == ["input", "swipe"]]
    assert len(swipes) == 1


@pytest.mark.asyncio
async def test_confirm_number_calls_human_tap(console: Console) -> None:
    nodes = parse_dump(CONFIRM)
    await telegram_app.confirm_number("emulator-5554", nodes)
    swipes = [call for call in console.shell_calls() if call[:2] == ["input", "swipe"]]
    assert len(swipes) == 1


@pytest.mark.asyncio
async def test_enter_code_calls_human_tap_and_human_type(console: Console) -> None:
    nodes = parse_dump(CODE)
    await telegram_app.enter_code("emulator-5554", nodes, "12345")
    swipes = [call for call in console.shell_calls() if call[:2] == ["input", "swipe"]]
    assert len(swipes) == 1
    typed = [call[2] for call in console.shell_calls() if call[:2] == ["input", "text"]]
    assert "".join(typed) == "12345"


@pytest.mark.asyncio
async def test_an_untypeable_code_is_refused_before_the_screen_is_touched(
    console: Console,
) -> None:
    with pytest.raises(EmulatorError) as refused:
        await telegram_app.enter_code("emulator-5554", parse_dump(CODE), "100%пароль")

    assert refused.value.code == "emulator_text_unsupported"
    assert console.calls == []


class _AlwaysTypo(random.Random):
    def random(self) -> float:
        return 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("screen", "step", "secret"),
    [(PASSWORD, telegram_app.enter_password, "s3cret!"), (CODE, telegram_app.enter_code, "12345")],
    ids=["password", "code"],
)
async def test_secrets_are_typed_without_simulated_typos(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    screen: str,
    step: Callable[[str, list[UiNode], str], Awaitable[None]],
    secret: str,
) -> None:
    monkeypatch.setattr(
        "core.emulator.adb._default_input_engine",
        HumanInputEngine(_AlwaysTypo(0)),
    )
    await step("emulator-5554", parse_dump(screen), secret)

    typed = [call[2] for call in console.shell_calls() if call[:2] == ["input", "text"]]
    assert "".join(typed).replace("\\", "") == secret
    assert ["input", "keyevent", "67"] not in console.shell_calls()
